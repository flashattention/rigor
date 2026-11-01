"""진단·로드맵 출력. docs/schemas.md §5."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from rigor.schemas.common import Citation, Citations, Provenance, StrictModel
from rigor.schemas.repo import DoneCriterion


class VerdictStatus(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"  # 근거 부족 → 사용자에게 질문, 지어내지 않음


class RequirementVerdict(StrictModel):
    requirement_id: str
    status: VerdictStatus
    citations: list[Citation] = Field(default_factory=list)
    rationale: str

    @model_validator(mode="after")
    def _met_needs_citation(self) -> RequirementVerdict:
        if self.status in (VerdictStatus.MET, VerdictStatus.PARTIAL) and not self.citations:
            raise ValueError(f"{self.status} verdict needs at least one citation")
        return self


class Grade(StrEnum):
    STRONG = "strong"  # 강력 추천
    APPLY = "apply"  # 지원 가능
    GAP_FIRST = "gap_first"  # 갭 보완 후
    NOT_RECOMMENDED = "not_recommended"  # 비추천


class GapType(StrEnum):
    EXPRESSION = "expression"  # (a) 표현으로 해결
    MISSING_FACT = "missing_fact"  # (b) 경험·프로젝트 필요
    KNOCKOUT = "knockout"  # (c) 결격


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class Gap(StrictModel):
    id: str
    requirement_id: str
    type: GapType
    severity: Severity
    next_action: str
    citations: Citations


class Weakness(StrictModel):
    category: str = Field(pattern=r"^W[0-9]+$")  # rubric §3.1
    text: str
    citations: Citations


class Redirect(StrictModel):
    role_id: str
    reason: str
    citations: Citations


class Alternative(StrictModel):
    text: str
    citations: Citations


class GradeTrace(StrictModel):
    rule_id: str = Field(pattern=r"^G[0-9]+$")
    inputs: dict[str, float | int | str]


class Diagnosis(StrictModel):
    grade: Grade
    grade_trace: GradeTrace
    verdicts: list[RequirementVerdict]
    gaps: list[Gap] = Field(default_factory=list)
    weaknesses: list[Weakness] = Field(default_factory=list)
    redirect: Redirect | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    questions_for_user: list[str] = Field(default_factory=list)
    provenance: Provenance | None = None

    @model_validator(mode="after")
    def _not_recommended_needs_alternative(self) -> Diagnosis:
        if self.grade is Grade.NOT_RECOMMENDED and not self.alternatives:
            raise ValueError("not_recommended diagnosis must include at least one alternative path")
        return self


class RoadmapKind(StrEnum):
    NEW_PROJECT = "new_project"
    IMPROVE_REPO = "improve_repo"


class RoadmapItem(StrictModel):
    kind: RoadmapKind
    title: str
    problem: str
    tech: list[str] = Field(default_factory=list)
    done_criteria: list[DoneCriterion] = Field(min_length=1)
    resume_line_example: str | None = None
    effort_days: float = Field(gt=0)
    priority: int = Field(ge=1)
    addresses_requirements: list[str] = Field(min_length=1)
    repo_id: int | None = None

    @model_validator(mode="after")
    def _improve_needs_repo(self) -> RoadmapItem:
        if self.kind is RoadmapKind.IMPROVE_REPO and self.repo_id is None:
            raise ValueError("improve_repo item needs repo_id")
        return self


class Roadmap(StrictModel):
    items: list[RoadmapItem] = Field(default_factory=list)

    @property
    def total_effort_days(self) -> float:
        return sum(i.effort_days for i in self.items)
