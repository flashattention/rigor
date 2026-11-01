"""E3: 진단 — 갭 분류, 서류 약점, 방향 전환, 대안, 질문 (rubric.md §3). 규칙만 쓴다.

모든 항목은 Citation을 가진다. 인용할 근거가 없는 지적은 만들지 않는다.
"""

from __future__ import annotations

import math
from collections import Counter

from rigor.core.grading import GradeInputs, core_competencies
from rigor.core.repo_verdict import RepoJudgement
from rigor.schemas.common import Citation
from rigor.schemas.diagnosis import (
    Alternative,
    Gap,
    GapType,
    Grade,
    Redirect,
    RequirementVerdict,
    Severity,
    VerdictStatus,
    Weakness,
)
from rigor.schemas.job import JobPosting, LegalCheck, RequirementKind
from rigor.schemas.profile import Attribution, ClaimType, FactCategory, Profile
from rigor.schemas.repo import ClaimResult, RepoAudit, RepoVerdict
from rigor.taxonomy import load_taxonomy

REDIRECT_MARGIN = 0.15  # 다른 직무 프로파일과의 유사도가 이만큼 더 높아야 제안 (관례값, M1b 튜닝)


def req_cite(rid: str, text: str | None = None) -> Citation:
    return Citation(kind="requirement", ref_id=rid, quote=text)


def classify_gaps(
    job: JobPosting, verdicts: list[RequirementVerdict], audits: dict[int, RepoAudit]
) -> list[Gap]:
    core = core_competencies(job)
    status = {v.requirement_id: v for v in verdicts}
    repo_deps = {d for a in audits.values() for d in a.signals.dependencies}
    gaps = []
    for r in job.requirements:
        if r.legal_check is LegalCheck.FLAG:
            continue
        v = status.get(r.id)
        st = v.status if v else VerdictStatus.UNKNOWN
        if st is VerdictStatus.MET:
            continue
        if r.kind is RequirementKind.KNOCKOUT:
            if st is VerdictStatus.UNMET:
                gaps.append(
                    Gap(
                        id=f"g_{r.id}",
                        requirement_id=r.id,
                        type=GapType.KNOCKOUT,
                        severity=Severity.CRITICAL,
                        citations=[req_cite(r.id, r.text)],
                        next_action="이 요건이 없는 공고를 찾거나, 요건 충족 계획을 세운다",
                    )
                )
            continue
        if r.kind is RequirementKind.REQUIRED:
            severity = Severity.CRITICAL if core & set(r.competencies) else Severity.MAJOR
        else:
            severity = Severity.MINOR
        cites = [req_cite(r.id, r.text)] + (list(v.citations) if v else [])
        expression = st is VerdictStatus.PARTIAL and set(r.skills) & repo_deps
        if expression:
            gaps.append(
                Gap(
                    id=f"g_{r.id}",
                    requirement_id=r.id,
                    type=GapType.EXPRESSION,
                    severity=severity,
                    citations=cites,
                    next_action="저장소에는 근거가 있으니 이력서 문장에 드러나게 고친다",
                )
            )
        else:
            gaps.append(
                Gap(
                    id=f"g_{r.id}",
                    requirement_id=r.id,
                    type=GapType.MISSING_FACT,
                    severity=severity,
                    citations=cites,
                    next_action="이력서 문장으로는 메울 수 없다 — 로드맵의 프로젝트로 근거를 만든다",
                )
            )
    return gaps


def weaknesses(
    profile: Profile, audits: dict[int, RepoAudit], judgements: list[RepoJudgement]
) -> list[Weakness]:
    out = []
    # 정합 전 프로필 기준 (근거 없는 수치를 지운 뒤에 "수치가 없다"고 하면 W2와 모순된다)
    projects = [f for f in profile.evidence_facts() if f.category is FactCategory.PROJECT]
    no_metric = [
        f
        for f in projects
        if not f.metrics and not any(c.type is ClaimType.METRIC for c in f.claims)
    ]
    if projects and len(no_metric) == len(projects):
        out.append(
            Weakness(
                category="W1",
                text="프로젝트에 성과 수치가 하나도 없다",
                citations=[Citation(kind="fact", ref_id=f.id) for f in no_metric[:3]],
            )
        )
    checks = [c for a in audits.values() for c in a.claim_checks]
    unsupported_metric = [
        c for c in checks if c.result is ClaimResult.NOT_FOUND and "수치" in (c.action or "")
    ]
    if unsupported_metric:
        out.append(
            Weakness(
                category="W2",
                text="저장소에서 측정 근거를 찾을 수 없는 수치가 있다",
                citations=[
                    Citation(kind="fact", ref_id=c.fact_id, quote=c.claim)
                    for c in unsupported_metric
                ],
            )
        )
    vague = [
        f
        for f in projects
        if f.attribution is Attribution.TEAM and not (f.role or f.contribution_note)
    ]
    if vague:
        out.append(
            Weakness(
                category="W3",
                text="팀 프로젝트인데 본인 역할이 적혀 있지 않다",
                citations=[Citation(kind="fact", ref_id=f.id) for f in vague],
            )
        )
    mismatched = [
        c
        for c in checks
        if c.result in (ClaimResult.NOT_FOUND, ClaimResult.CONTRADICTED)
        and c not in unsupported_metric
    ]
    if mismatched:
        out.append(
            Weakness(
                category="W5",
                text="이력서 서술과 저장소 내용이 다른 항목이 있다",
                citations=[
                    Citation(kind="fact", ref_id=c.fact_id, quote=c.claim) for c in mismatched
                ],
            )
        )
    if judgements and not any(j.verdict is RepoVerdict.SHOWCASE for j in judgements):
        out.append(
            Weakness(
                category="W6",
                text="이 공고의 대표 프로젝트로 내세울 저장소가 아직 없다",
                citations=[
                    Citation(kind="repo_evidence", ref_id=f"e_{j.repo_id}_summary")
                    for j in judgements
                ],
            )
        )
    return out


TUTORIAL_WEIGHT = 0.5  # taxonomy §3: 튜토리얼 = 깊이 1, 실제 적용 = 깊이 2 → 1/2


def evidence_profile_vector(
    profile: Profile, audits: dict[int, RepoAudit], tutorial_repos: set[int] | None = None
) -> Counter[str]:
    """지원자 근거의 역량 분포: 확인된 fact의 기술 + 저장소 의존성 (튜토리얼 근거는 절반)."""
    tax = load_taxonomy()
    tutorial_repos = tutorial_repos or set()
    vec: Counter[str] = Counter()
    for f in profile.evidence_facts():
        w = TUTORIAL_WEIGHT if f.repo_link and f.repo_link.repo_id in tutorial_repos else 1.0
        for s in f.skills:
            for c in tax.skill_competencies.get(s, ()):
                vec[c] += w
    for rid, a in audits.items():
        w = TUTORIAL_WEIGHT if rid in tutorial_repos else 1.0
        for d in a.signals.dependencies:
            for c in tax.skill_competencies.get(d, ()):
                vec[c] += w
    return vec


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in set(a) | set(b))
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def suggest_redirect(
    job: JobPosting,
    grade: Grade,
    profile: Profile,
    audits: dict[int, RepoAudit],
    judgements: list[RepoJudgement] | None = None,
) -> Redirect | None:
    """적합도가 낮을 때만, 근거가 다른 직무에 뚜렷이 더 가까우면 제안한다."""
    if grade not in (Grade.GAP_FIRST, Grade.NOT_RECOMMENDED) or not job.role_ids:
        return None
    tax = load_taxonomy()
    tutorial = {j.repo_id for j in judgements or [] if any(b.startswith("X4") for b in j.blockers)}
    vec = dict(evidence_profile_vector(profile, audits, tutorial))
    sims = {role: _cos(vec, weights) for role, weights in tax.roles.items()}
    current = sims.get(job.role_ids[0], 0.0)
    best = max(sims, key=sims.get)
    if best == job.role_ids[0] or sims[best] - current < REDIRECT_MARGIN:
        return None
    top = [c for c, _ in Counter(vec).most_common(3)]
    cites = [
        Citation(kind="fact", ref_id=f.id)
        for f in profile.evidence_facts()
        if any(c in top for s in f.skills for c in tax.skill_competencies.get(s, ()))
    ][:3]
    if not cites:
        return None
    return Redirect(
        role_id=best,
        citations=cites,
        reason=f"근거의 역량 분포({', '.join(top)})가 이 공고보다 {best} 직무에 더 가깝다 "
        f"(유사도 {sims[best]:.2f} vs {current:.2f})",
    )


def alternatives(
    job: JobPosting, grade: Grade, gi: GradeInputs, redirect: Redirect | None
) -> list[Alternative]:
    if grade is not Grade.NOT_RECOMMENDED:
        return []
    out = []
    if redirect:
        out.append(
            Alternative(
                text=f"{redirect.role_id} 직무 공고에 지원하면 지금 근거로 더 강하다",
                citations=redirect.citations,
            )
        )
    for rid in gi.knockout_unmet:
        r = job.requirement(rid)
        out.append(
            Alternative(
                text=f"'{r.text}' 요건이 없는 같은 직무 공고를 찾는다",
                citations=[req_cite(rid, r.text)],
            )
        )
    if not out:
        missing = (
            gi.critical_gaps
            or [r.id for r in job.requirements if r.kind is RequirementKind.REQUIRED][:1]
        )
        out.append(
            Alternative(
                text="부족한 핵심 요건을 로드맵 프로젝트로 채운 뒤 다시 진단한다",
                citations=[req_cite(r) for r in missing],
            )
        )
    return out


def questions(job: JobPosting, verdicts: list[RequirementVerdict]) -> list[str]:
    return [
        f"'{job.requirement(v.requirement_id).text}' 관련 경험이 있다면 알려 주세요 (근거가 없어 판단하지 않았습니다)"
        for v in verdicts
        if v.status is VerdictStatus.UNKNOWN
    ]
