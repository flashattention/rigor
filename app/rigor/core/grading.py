"""E2: 등급 계산 — LLM이 아니라 규칙 (ADR-0008, rubric.md §2).

같은 요건 판정이면 항상 같은 등급이 나온다. 사용자가 압박해도 새 근거가 없으면 등급은 바뀌지 않는다.
기준값의 근거: [B1] 정의, [B2] TalentWorks 요건 충족률 분석(40%/60%, 잠정), [B3] 오류 비용 비대칭.
"""

from __future__ import annotations

from dataclasses import dataclass

from rigor.schemas.diagnosis import Grade, GradeTrace, RequirementVerdict, VerdictStatus
from rigor.schemas.job import JobPosting, LegalCheck, RequirementKind
from rigor.taxonomy import load_taxonomy

NOT_RECOMMENDED_BELOW = 0.4  # [B2]
GAP_FIRST_BELOW = 0.6  # [B2]
PARTIAL_WEIGHT = 0.5  # 관례값, 민감도 분석 대상

_CREDIT = {VerdictStatus.MET: 1.0, VerdictStatus.PARTIAL: PARTIAL_WEIGHT}


@dataclass(frozen=True)
class GradeInputs:
    """등급 규칙의 입력. 보호 속성 요건(legal_check=flag)을 뺀 값이다."""

    cov_req: float
    knockout_unmet: list[str]
    knockout_unknown: list[str]
    critical_gaps: list[str]
    core_required: list[str]
    showcase_core: bool


def core_competencies(job: JobPosting) -> set[str]:
    """주 직무의 핵심 역량. role_ids가 비면 빈 집합이라 G3·G5가 성립하지 않는다."""
    tax = load_taxonomy()
    out: set[str] = set()
    for role in job.role_ids[:1]:  # 공고의 주 직무 기준
        out |= tax.core_competencies(role)
    return out


def grade_inputs(
    job: JobPosting, verdicts: list[RequirementVerdict], showcase_requirements: set[str]
) -> GradeInputs:
    """showcase_requirements: 대표(showcase) 저장소가 근거로 인용된 요건 id."""
    by_id = {v.requirement_id: v.status for v in verdicts}
    core = core_competencies(job)
    usable = [r for r in job.requirements if r.legal_check is LegalCheck.OK]
    required = [r for r in usable if r.kind is RequirementKind.REQUIRED]
    knockouts = [r for r in usable if r.kind is RequirementKind.KNOCKOUT]
    status = {r.id: by_id.get(r.id, VerdictStatus.UNKNOWN) for r in usable}
    cov = sum(_CREDIT.get(status[r.id], 0.0) for r in required) / len(required) if required else 1.0
    core_required = [r.id for r in required if core & set(r.competencies)]
    # rubric.md §1: unknown은 등급 계산에서 unmet으로 본다 (결격 판정만 예외)
    critical = [
        rid for rid in core_required if status[rid] in (VerdictStatus.UNMET, VerdictStatus.UNKNOWN)
    ]
    return GradeInputs(
        cov_req=round(cov, 4),
        knockout_unmet=[r.id for r in knockouts if status[r.id] is VerdictStatus.UNMET],
        knockout_unknown=[r.id for r in knockouts if status[r.id] is VerdictStatus.UNKNOWN],
        critical_gaps=critical,
        core_required=core_required,
        showcase_core=bool(set(core_required) & showcase_requirements),
    )


def compute_grade(
    job: JobPosting, verdicts: list[RequirementVerdict], showcase_requirements: set[str]
) -> tuple[Grade, GradeTrace, GradeInputs]:
    """rubric.md §2의 규칙을 G1부터 적용한다. 먼저 맞는 규칙이 등급이므로 순서가 의미를 가진다."""
    gi = grade_inputs(job, verdicts, showcase_requirements)
    by_id = {v.requirement_id: v.status for v in verdicts}
    required = [
        r.id
        for r in job.requirements
        if r.kind is RequirementKind.REQUIRED and r.legal_check is LegalCheck.OK
    ]
    trace_in: dict[str, float | int | str] = {
        "cov_req": gi.cov_req,
        "critical_gaps": len(gi.critical_gaps),
        "knockout_unmet": len(gi.knockout_unmet),
    }

    def done(grade: Grade, rule: str) -> tuple[Grade, GradeTrace, GradeInputs]:
        return grade, GradeTrace(rule_id=rule, inputs=trace_in), gi

    if gi.knockout_unmet:  # G1 [B1] (unknown은 결격으로 보지 않고 질문으로 돌린다 [B3])
        return done(Grade.NOT_RECOMMENDED, "G1")
    if gi.cov_req < NOT_RECOMMENDED_BELOW:  # G2 [B2]
        return done(Grade.NOT_RECOMMENDED, "G2")
    if gi.critical_gaps:  # G3 [B2 단서: 핵심 하드 스킬은 만회 어려움]
        return done(Grade.GAP_FIRST, "G3")
    if gi.cov_req < GAP_FIRST_BELOW:  # G4 [B2]
        return done(Grade.GAP_FIRST, "G4")
    all_filled = all(by_id.get(r) in (VerdictStatus.MET, VerdictStatus.PARTIAL) for r in required)
    core_met = all(by_id.get(r) is VerdictStatus.MET for r in gi.core_required)
    if all_filled and core_met and gi.showcase_core:  # G5 [B1]
        return done(Grade.STRONG, "G5")
    return done(Grade.APPLY, "G6")
