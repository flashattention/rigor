"""E1: 요건별 판정 (LLM) + 규칙 검증.

LLM은 요건마다 met/partial/unmet/unknown과 인용만 낸다. 규칙이 하는 일:
- 존재하지 않는 근거 id 인용은 버린다. met/partial인데 유효한 인용이 없으면 unknown으로 낮춘다.
- 학위 결격 요건은 정규화된 학력으로 규칙 판정한다 (LLM 판정을 덮어쓴다).
- 보호 속성 요건(legal_check=flag)은 판정하지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import Field

from rigor.llm.client import LLMClient
from rigor.prompts import data_block, load_prompt
from rigor.schemas.common import Citation, DraftModel
from rigor.schemas.diagnosis import RequirementVerdict, VerdictStatus
from rigor.schemas.job import JobPosting, KnockoutType, LegalCheck, RequirementKind
from rigor.schemas.profile import Degree, FactCategory, Profile
from rigor.schemas.repo import ClaimResult, RepoAudit, RepoEvidence

TASK = "requirement_verdict"
_DEGREE_RANK = {
    Degree.NONE: 0,
    Degree.ASSOCIATE: 1,
    Degree.BACHELOR: 2,
    Degree.MASTER: 3,
    Degree.PHD: 4,
}
_REQUIRED_DEGREE = [
    (re.compile(r"박사|ph\.?d|doctor", re.I), Degree.PHD),
    (re.compile(r"석사|master|m\.s\.", re.I), Degree.MASTER),
    (re.compile(r"학사|bachelor|대졸|4년제", re.I), Degree.BACHELOR),
]


class CitationDraft(DraftModel):
    ref_id: str
    quote: str | None = None


class VerdictDraft(DraftModel):
    requirement_id: str
    status: VerdictStatus
    citations: list[CitationDraft] = Field(default_factory=list)
    rationale: str


class VerdictsDraft(DraftModel):
    verdicts: list[VerdictDraft]


@dataclass
class EvidenceBundle:
    text: str
    fact_ids: set[str]
    repo_evidence: dict[str, RepoEvidence]
    fact_repo: dict[str, int]  # fact → 연결된 저장소


@dataclass
class MatchResult:
    verdicts: list[RequirementVerdict]
    warnings: list[str] = field(default_factory=list)
    prompt_id: str = ""


def summary_evidence(audit: RepoAudit) -> RepoEvidence:
    s = audit.signals
    c = audit.contribution
    parts = [
        f"저장소 {audit.full_name}",
        f"의존성: {', '.join(s.dependencies) or '없음'}",
        f"테스트 파일 {s.tests.file_count}개" if s.tests.exists else "테스트 없음",
        "CI 있음" if s.ci.exists else "CI 없음",
        f"README 섹션: {', '.join(s.readme.sections) or '없음'}",
    ]
    if c:
        parts.append(f"본인 커밋 {c.user_commits}/{c.total_commits}")
    return RepoEvidence(
        id=f"e_{audit.repo_id}_summary", repo_id=audit.repo_id, quote="; ".join(parts)
    )


def build_evidence(profile: Profile, audits: dict[int, RepoAudit]) -> EvidenceBundle:
    lines: list[str] = []
    fact_repo: dict[str, int] = {}
    repo_ev: dict[str, RepoEvidence] = {}
    for f in profile.evidence_facts():
        head = f"[{f.id}] {f.category.value}: {f.content}"
        extra = [x for x in (f.role and f"역할 {f.role}", f.attribution.value) if x]
        if f.education_normalized:
            e = f.education_normalized
            extra.append(f"학위 {e.degree.value} {e.field or ''} {e.status.value}")
        lines.append(head + (f" ({', '.join(extra)})" if extra else ""))
        for c in f.claims:
            lines.append(f"    - ({c.type.value}) {c.text}")
        if f.repo_link and f.repo_link.repo_id in audits:
            fact_repo[f.id] = f.repo_link.repo_id
    for rid, audit in audits.items():
        if rid not in fact_repo.values():
            continue
        summary = summary_evidence(audit)
        repo_ev[summary.id] = summary
        lines.append(f"[{summary.id}] {summary.quote}")
        supported = {c.claim_id for c in audit.claim_checks if c.result is ClaimResult.SUPPORTED}
        for chk in audit.claim_checks:
            if chk.claim_id not in supported:
                continue
            for cite in chk.evidence:
                ev = next((e for e in audit.evidence if e.id == cite.ref_id), None)
                if ev and ev.id not in repo_ev:
                    repo_ev[ev.id] = ev
                    where = f"{ev.path}:{ev.line_start}" if ev.path else ""
                    lines.append(f"[{ev.id}] '{chk.claim}' 확인 {where} {ev.quote or ''}".rstrip())
    return EvidenceBundle(
        "\n".join(lines), {f.id for f in profile.evidence_facts()}, repo_ev, fact_repo
    )


def _kind(ref: str) -> str | None:
    if ref.startswith("f_"):
        return "fact"
    if ref.startswith("e_"):
        return "repo_evidence"
    return None


def _degree_verdict(req_text: str, profile: Profile) -> RequirementVerdict | None:
    need = next((d for pattern, d in _REQUIRED_DEGREE if pattern.search(req_text)), None)
    edu = [
        f
        for f in profile.evidence_facts()
        if f.category is FactCategory.EDUCATION and f.education_normalized
    ]
    if need is None or not edu:
        return None
    best = max(edu, key=lambda f: _DEGREE_RANK[f.education_normalized.degree])
    ok = _DEGREE_RANK[best.education_normalized.degree] >= _DEGREE_RANK[need]
    return RequirementVerdict(
        requirement_id="",
        status=VerdictStatus.MET if ok else VerdictStatus.UNMET,
        citations=[Citation(kind="fact", ref_id=best.id)],
        rationale=f"확인된 최종 학위 {best.education_normalized.degree.value}, 요건 {need.value} 이상 (규칙 판정)",
    )


def match_requirements(
    client: LLMClient, job: JobPosting, profile: Profile, audits: dict[int, RepoAudit]
) -> tuple[MatchResult, EvidenceBundle]:
    bundle = build_evidence(profile, audits)
    prompt = load_prompt(TASK)
    req_lines = [
        f"[{r.id}] ({r.kind.value}, 깊이 {r.effective_min_depth}) {r.text}"
        for r in job.requirements
        if r.legal_check is LegalCheck.OK
    ]
    result = client.structured(
        TASK,
        system=prompt.text,
        messages=[
            {
                "role": "user",
                "content": data_block("requirements", "\n".join(req_lines))
                + "\n\n"
                + data_block("evidence", bundle.text),
            }
        ],
        schema=VerdictsDraft,
    )
    drafts = {d.requirement_id: d for d in result.value.verdicts}
    warnings = [
        f"unknown requirement id in output: {k}"
        for k in drafts
        if k not in {r.id for r in job.requirements}
    ]
    verdicts = []
    for r in job.requirements:
        if r.legal_check is LegalCheck.FLAG:
            verdicts.append(
                RequirementVerdict(
                    requirement_id=r.id,
                    status=VerdictStatus.UNKNOWN,
                    rationale="보호 속성 기반 요건이라 판정하지 않았다",
                )
            )
            continue
        if r.kind is RequirementKind.KNOCKOUT and r.knockout_type is KnockoutType.DEGREE:
            rule = _degree_verdict(r.text, profile)
            if rule:
                verdicts.append(rule.model_copy(update={"requirement_id": r.id}))
                continue
        d = drafts.get(r.id)
        if d is None:
            warnings.append(f"{r.id}: no verdict in output → unknown")
            verdicts.append(
                RequirementVerdict(
                    requirement_id=r.id, status=VerdictStatus.UNKNOWN, rationale="판정 누락"
                )
            )
            continue
        cites = []
        for c in d.citations:
            kind = _kind(c.ref_id)
            if (kind == "fact" and c.ref_id in bundle.fact_ids) or (
                kind == "repo_evidence" and c.ref_id in bundle.repo_evidence
            ):
                cites.append(Citation(kind=kind, ref_id=c.ref_id, quote=c.quote))
            else:
                warnings.append(f"{r.id}: invalid citation {c.ref_id} dropped")
        status = d.status
        if status in (VerdictStatus.MET, VerdictStatus.PARTIAL) and not cites:
            warnings.append(f"{r.id}: {status.value} without valid citation → unknown")
            status = VerdictStatus.UNKNOWN
        verdicts.append(
            RequirementVerdict(
                requirement_id=r.id, status=status, citations=cites, rationale=d.rationale
            )
        )
    return MatchResult(verdicts, warnings, prompt.id), bundle


def cited_requirements_by_repo(
    verdicts: list[RequirementVerdict], bundle: EvidenceBundle
) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    for v in verdicts:
        if v.status not in (VerdictStatus.MET, VerdictStatus.PARTIAL):
            continue
        for c in v.citations:
            rid = (
                bundle.fact_repo.get(c.ref_id)
                if c.kind == "fact"
                else (
                    bundle.repo_evidence[c.ref_id].repo_id
                    if c.ref_id in bundle.repo_evidence
                    else None
                )
            )
            if rid is not None:
                out.setdefault(rid, set()).add(v.requirement_id)
    return out
