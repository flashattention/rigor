"""A5: 공고 텍스트 → JobPosting. LLM이 구조화하고, 규칙이 검증·보정한다.

규칙 검증:
- 요건 문장이 원문에 글자 그대로 있는지 (없으면 경고 — 요약·지어낸 요건 탐지)
- knockout이면 해당 유형의 키워드가 문장에 있는지 (없으면 required로 낮추고 경고)
- 보호 속성 기반 요건은 legal_check=flag (판정에서 제외, ADR-0014)
- 인젝션 의심 문장은 요건에서 제거
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from pydantic import Field

from rigor.llm.client import LLMClient
from rigor.prompts import data_block, load_prompt
from rigor.safety.injection import find_injection_lines, is_injection
from rigor.schemas.common import DraftModel
from rigor.schemas.job import (
    ExperienceYears,
    JobPosting,
    JobSource,
    KnockoutType,
    LegalCheck,
    Requirement,
    RequirementKind,
)
from rigor.taxonomy import load_taxonomy

TASK = "jd_parse"

# knockout 문장에 있어야 할 유형별 키워드. 없으면 LLM의 결격 판단을 믿지 않고 required로 낮춘다.
# OTHER는 어떤 문장이든 통과한다.
_KNOCKOUT_KEYWORDS = {
    KnockoutType.DEGREE: r"학사|석사|박사|학위|전공|degree|bachelor|master|ph\.?d",
    KnockoutType.CERTIFICATION: r"자격증|자격|기사|certif|license",
    KnockoutType.LOCATION: r"근무지|상주|출근|거주|on-?site|relocat",
    KnockoutType.WORK_AUTHORIZATION: r"비자|취업\s*자격|체류|visa|work\s*authori|sponsor",
    KnockoutType.LANGUAGE_CERT: r"토익|토플|오픽|toeic|toefl|opic|ielts|jlpt|hsk",
    KnockoutType.OTHER: r".",
}
_PROTECTED = re.compile(
    r"남성|여성|남자|여자|성별|만\s*\d{1,2}\s*세|\d{2}\s*세\s*(이하|미만|이상)|나이|연령|"
    r"병역\s*필|군필|미혼|기혼|외모|신장|출신\s*(지역|학교)"
)


class RequirementDraft(DraftModel):
    text: str = Field(min_length=1)
    kind: RequirementKind
    knockout_type: KnockoutType | None = None
    competencies: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    min_depth: int | None = Field(default=None, ge=0, le=3)


class JobDraft(DraftModel):
    company: str
    title: str
    role_ids: list[str] = Field(default_factory=list)
    language: str = "ko"
    blind: bool = False
    deadline: str | None = None
    experience_min_years: float | None = Field(default=None, ge=0)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[RequirementDraft] = Field(min_length=1)


@dataclass
class JDParseResult:
    job: JobPosting
    warnings: list[str] = field(default_factory=list)
    prompt_id: str = ""
    cost_usd: float = 0.0


_CODE = re.compile(r"^\s*(C\d{2})\b")


def _competency_code(raw: str) -> str | None:
    """ "C08", "C08 백엔드·API" 모두 C08로. 분류 체계에 없는 코드는 None."""
    m = _CODE.match(raw)
    code = m.group(1) if m else None
    return code if code in load_taxonomy().competencies else None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def build_job(
    draft: JobDraft, text: str, job_id: str, owner_user_id: str | None = None
) -> tuple[JobPosting, list[str]]:
    """LLM 초안 + 원문 → JobPosting (순수 함수, 테스트 대상)."""
    taxonomy = load_taxonomy()
    warnings = [
        f"injection-like line in posting: {line[:60]}" for line in find_injection_lines(text)
    ]
    source = _norm(text)
    requirements: list[Requirement] = []
    for d in draft.requirements:
        if is_injection(d.text):
            warnings.append("injection-like requirement dropped")
            continue
        rid = f"r_{len(requirements) + 1}"
        if _norm(d.text) not in source:
            warnings.append(f"{rid}: text is not a verbatim span of the posting")
        kind, ktype = d.kind, d.knockout_type
        if kind is RequirementKind.KNOCKOUT:
            pattern = _KNOCKOUT_KEYWORDS.get(ktype) if ktype else None
            if pattern is None or not re.search(pattern, d.text, re.IGNORECASE):
                warnings.append(f"{rid}: knockout without matching keyword → required")
                kind, ktype = RequirementKind.REQUIRED, None
        elif ktype is not None:
            ktype = None
        codes = [_competency_code(c) for c in d.competencies]
        unknown = [c for c, code in zip(d.competencies, codes, strict=True) if code is None]
        if unknown:
            warnings.append(f"{rid}: unknown competencies dropped {unknown}")
        requirements.append(
            Requirement(
                id=rid,
                text=d.text,
                kind=kind,
                knockout_type=ktype,
                competencies=sorted({c for c in codes if c}),
                skills=sorted({taxonomy.normalize_skill(s) for s in d.skills}),
                min_depth=d.min_depth,
                legal_check=LegalCheck.FLAG if _PROTECTED.search(d.text) else LegalCheck.OK,
            )
        )
    roles = [r for r in draft.role_ids if r in taxonomy.roles]
    if len(roles) != len(draft.role_ids):
        warnings.append(f"unknown role ids dropped: {set(draft.role_ids) - set(roles)}")
    deadline = None
    if draft.deadline:
        try:
            deadline = date.fromisoformat(draft.deadline)
        except ValueError:
            warnings.append("deadline not ISO format, ignored")
    job = JobPosting(
        id=job_id,
        owner_user_id=owner_user_id,
        source=JobSource(),
        raw_text_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
        company=draft.company,
        title=draft.title,
        role_ids=roles,
        language=draft.language,
        blind=draft.blind or ("블라인드" in text),
        deadline=deadline,
        experience_years=ExperienceYears(min=draft.experience_min_years),
        requirements=requirements,
        responsibilities=draft.responsibilities,
        parse_warnings=warnings,
    )
    return job, warnings


def parse_jd(
    client: LLMClient, text: str, job_id: str, owner_user_id: str | None = None
) -> JDParseResult:
    """공고 원문을 LLM으로 구조화하고 build_job으로 검증한다. LLM 오류는 그대로 전파한다."""
    prompt = load_prompt(TASK)
    result = client.structured(
        TASK,
        system=prompt.text,
        messages=[{"role": "user", "content": data_block("job_posting", text)}],
        schema=JobDraft,
    )
    job, warnings = build_job(result.value, text, job_id, owner_user_id)
    return JDParseResult(job, warnings, prompt.id, result.cost_usd)
