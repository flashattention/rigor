"""A3·A4: 이력서 텍스트 → fact·claim 추출 → 사용자 확인 (ADR-0017).

LLM은 마스킹된 텍스트만 본다. ID 부여, 토큰 복원, 기술명 정규화, 날짜 해석은 규칙이 한다.
추출 직후 fact는 모두 unconfirmed이고, 사용자 확인을 거친 것만 판정 근거가 된다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from pydantic import Field

from rigor.ingest.privacy import MaskResult
from rigor.llm.client import LLMClient
from rigor.prompts import data_block, load_prompt
from rigor.safety.injection import is_injection
from rigor.schemas.common import StrictModel
from rigor.schemas.profile import (
    Attribution,
    Claim,
    ClaimType,
    EducationNormalized,
    Fact,
    FactCategory,
    FactStatus,
    Metric,
    Period,
    Profile,
    RepoLink,
)
from rigor.taxonomy import load_taxonomy

TASK = "fact_extract"


class ClaimDraft(StrictModel):
    type: ClaimType
    text: str = Field(min_length=1)


class FactDraft(StrictModel):
    category: FactCategory
    content: str = Field(min_length=1)
    start: str | None = None
    end: str | None = None
    ongoing: bool = False
    organization: str | None = None
    role: str | None = None
    attribution: Attribution = Attribution.MIXED
    skills: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    repo_refs: list[str] = Field(default_factory=list)
    education: EducationNormalized | None = None
    claims: list[ClaimDraft] = Field(default_factory=list)
    uncertain: list[str] = Field(default_factory=list)


class ProfileDraft(StrictModel):
    facts: list[FactDraft]


@dataclass
class ExtractionResult:
    profile: Profile
    warnings: list[str] = field(default_factory=list)
    uncertain: dict[str, list[str]] = field(default_factory=dict)  # fact_id → 모호한 필드
    prompt_id: str = ""
    cost_usd: float = 0.0


_YM = re.compile(r"^\s*(\d{4})[.\-/](\d{1,2})(?:[.\-/](\d{1,2}))?\s*$")


_TOKEN = re.compile(r"\[?\s*(REPO_\d+)\s*\]?", re.IGNORECASE)


def _normalize_token(raw: str) -> str | None:
    """모델이 대괄호를 빼먹어도("REPO_1") 같은 토큰으로 본다. 토큰이 아니면 None."""
    m = _TOKEN.fullmatch(raw.strip())
    return f"[{m.group(1).upper()}]" if m else None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = _YM.match(raw)
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_profile(
    draft: ProfileDraft, mask: MaskResult, profile_id: str
) -> tuple[Profile, list[str], dict[str, list[str]]]:
    """LLM 초안 → Profile (순수 함수, 테스트 대상)."""
    taxonomy = load_taxonomy()
    warnings: list[str] = []
    uncertain: dict[str, list[str]] = {}
    facts: list[Fact] = []
    for i, d in enumerate(draft.facts, start=1):
        fid = f"f_{i}"
        if is_injection(d.content):
            warnings.append(f"injection-like fact dropped: {d.content[:40]}")
            continue
        repo_link = None
        for ref in d.repo_refs:
            token = _normalize_token(ref)
            if token in mask.repo_urls:
                repo_link = RepoLink(url=mask.repo_urls[token])
                break
            if token:
                warnings.append(f"{fid}: unknown repo token {ref}")
        claims = []
        for j, c in enumerate(d.claims, start=1):
            if is_injection(c.text):
                warnings.append(f"{fid}: injection-like claim dropped")
                continue
            claims.append(Claim(id=f"c_{i}_{j}", fact_id=fid, type=c.type, text=c.text))
        start, end = _parse_date(d.start), _parse_date(d.end)
        if start and end and end < start:
            warnings.append(f"{fid}: end before start, dates cleared")
            start = end = None
            uncertain.setdefault(fid, []).append("period")
        facts.append(
            Fact(
                id=fid,
                category=d.category,
                content=d.content,
                period=Period(start=start, end=end, ongoing=d.ongoing),
                organization=d.organization,
                education_normalized=d.education,
                role=d.role,
                attribution=d.attribution,
                skills=sorted({taxonomy.normalize_skill(s) for s in d.skills}),
                metrics=d.metrics,
                repo_link=repo_link,
                status=FactStatus.UNCONFIRMED,
                source="resume_extract",
                claims=claims,
            )
        )
        if d.uncertain:
            uncertain.setdefault(fid, []).extend(d.uncertain)
    return Profile(id=profile_id, facts=facts), warnings, uncertain


def extract_profile(client: LLMClient, mask: MaskResult, profile_id: str) -> ExtractionResult:
    prompt = load_prompt(TASK)
    result = client.structured(
        TASK,
        system=prompt.text,
        messages=[{"role": "user", "content": data_block("resume", mask.text)}],
        schema=ProfileDraft,
    )
    profile, warnings, uncertain = build_profile(result.value, mask, profile_id)
    return ExtractionResult(profile, warnings, uncertain, prompt.id, result.cost_usd)


Decider = Callable[[Fact, list[str]], FactStatus]


def confirm_facts(extraction: ExtractionResult, decide: Decider) -> Profile:
    """A4: 사용자가 fact마다 confirmed/rejected를 고른다. decide는 CLI·웹·테스트가 주입한다."""
    facts = []
    for f in extraction.profile.facts:
        status = decide(f, extraction.uncertain.get(f.id, []))
        if status not in (FactStatus.CONFIRMED, FactStatus.REJECTED, FactStatus.UNCONFIRMED):
            raise ValueError(f"invalid confirmation status: {status}")
        facts.append(f.model_copy(update={"status": status}))
    return extraction.profile.model_copy(update={"facts": facts})
