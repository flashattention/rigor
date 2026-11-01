"""GitHub 감사·연결·정합. docs/schemas.md §4."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from rigor.schemas.common import Citation, Citations, Provenance, StrictModel
from rigor.schemas.profile import UnlinkedReason


class ReadmeSignals(StrictModel):
    exists: bool = False
    length: int = 0
    sections: list[str] = Field(default_factory=list)  # problem|design|setup|results|...


class TestSignals(StrictModel):
    exists: bool = False
    frameworks: list[str] = Field(default_factory=list)
    file_count: int = 0


class CISignals(StrictModel):
    exists: bool = False
    providers: list[str] = Field(default_factory=list)


class DeploySignals(StrictModel):
    config_files: list[str] = Field(default_factory=list)
    homepage_url: str | None = None


class CommitSignals(StrictModel):
    total: int = 0
    user: int = 0
    first_at: str | None = None
    last_at: str | None = None
    active_weeks: int = 0


class RepoSignals(StrictModel):
    """결정론으로 계산한 신호. LLM이 만들지 않는다."""

    readme: ReadmeSignals = Field(default_factory=ReadmeSignals)
    tests: TestSignals = Field(default_factory=TestSignals)
    ci: CISignals = Field(default_factory=CISignals)
    license: str | None = None
    deploy: DeploySignals = Field(default_factory=DeploySignals)
    languages: dict[str, int] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    commits: CommitSignals = Field(default_factory=CommitSignals)
    size_kb: int = 0
    injection_suspects: int = 0


class RepoEvidence(StrictModel):
    id: str = Field(pattern=r"^e_[A-Za-z0-9_]+$")
    repo_id: int
    path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    commit_sha: str | None = None
    quote: str | None = None


class ClaimResult(StrEnum):
    SUPPORTED = "supported"
    NOT_FOUND = "not_found"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class CheckMethod(StrEnum):
    RULE = "rule"
    RETRIEVAL = "retrieval"
    AGENT = "agent"


class ClaimCheck(StrictModel):
    claim_id: str
    fact_id: str
    claim: str
    result: ClaimResult
    method: CheckMethod
    evidence: list[Citation] = Field(default_factory=list)
    action: str | None = None

    @model_validator(mode="after")
    def _evidence_required(self) -> ClaimCheck:
        if self.result in (ClaimResult.SUPPORTED, ClaimResult.CONTRADICTED) and not self.evidence:
            raise ValueError(f"{self.result} claim check needs at least one citation")
        return self


class ContributionCaveat(StrEnum):
    SQUASH_MERGE = "squash_merge"
    PAIR_PROGRAMMING = "pair_programming"
    BULK_MOVE = "bulk_move"
    UNMAPPED_EMAILS = "unmapped_emails"


class AreaContribution(StrictModel):
    path_prefix: str
    user_changes: int = Field(ge=0)
    total_changes: int = Field(ge=0)


class ContributionAnalysis(StrictModel):
    """판정 입력이 아니다. 정합 선택지와 면접 준비 카드의 근거로만 쓴다."""

    repo_id: int
    user_commits: int = Field(ge=0)
    total_commits: int = Field(ge=0)
    areas: list[AreaContribution] = Field(default_factory=list)
    active_period: tuple[str, str] | None = None
    prs_authored: int = 0
    prs_reviewed: int = 0
    ai_coauthored_commits: int = 0
    agent_bot_prs: int = 0
    caveats: list[ContributionCaveat] = Field(default_factory=list)

    @property
    def user_share(self) -> float:
        return self.user_commits / self.total_commits if self.total_commits else 0.0


class DoneCriterion(StrictModel):
    """재감사의 결정론 신호로 자동 확인 가능한 완료 기준. 예: tests.exists == true."""

    signal_path: str = Field(pattern=r"^[a-z_]+(\.[a-z_]+)*$")
    op: Literal["==", "!=", ">=", "<=", "contains"]
    value: Any


class Impact(StrEnum):
    LOW = "low"
    MED = "med"
    HIGH = "high"


class Improvement(StrictModel):
    id: str
    title: str
    description: str
    done_criteria: list[DoneCriterion] = Field(min_length=1)
    effort_days: float = Field(gt=0)
    impact: Impact
    citations: Citations


class ExposureKind(StrEnum):
    SECRET = "secret"
    REAL_NAME = "real_name"
    CONTACT = "contact"
    SCHOOL = "school"
    ORG_PRIVATE_DATA = "org_private_data"


class ExposureFinding(StrictModel):
    kind: ExposureKind
    path: str
    line: int | None = None
    detail: str  # 값 자체는 담지 않는다 (예: "AWS access key 패턴")


class RepoVerdict(StrEnum):
    SHOWCASE = "showcase"
    IMPROVE = "improve"
    DROP = "drop"


class RepoAudit(StrictModel):
    repo_id: int
    full_name: str
    visibility: Literal["public", "private"] = "public"
    head_sha: str
    is_fork: bool = False
    signals: RepoSignals
    contribution: ContributionAnalysis | None = None
    evidence: list[RepoEvidence] = Field(default_factory=list)
    claim_checks: list[ClaimCheck] = Field(default_factory=list)
    exposure_findings: list[ExposureFinding] = Field(default_factory=list)
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    verdict_for_jd: RepoVerdict | None = None
    improvements: list[Improvement] = Field(default_factory=list)
    scan_scope: Literal["api_partial", "full_clone", "local"] = "api_partial"
    provenance: Provenance | None = None


class RepoCandidate(StrictModel):
    repo_id: int
    full_name: str
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)


class UnlinkedProposal(StrEnum):
    ADD_EVIDENCE_OR_REDUCE = "add_evidence_or_reduce"
    KEEP_WITH_INTERVIEW_PREP = "keep_with_interview_prep"
    PRIVATE_AUDIT = "private_audit"
    LINK = "link"


class UnlinkedItem(StrictModel):
    fact_id: str
    candidates: list[RepoCandidate] = Field(default_factory=list, max_length=3)
    user_reason: UnlinkedReason | None = None
    chosen_repo_id: int | None = None
    proposal: UnlinkedProposal | None = None


class Mismatch(StrEnum):
    MISSING_IN_CODE = "missing_in_code"
    METRIC_UNSUPPORTED = "metric_unsupported"
    CONTRIBUTION_OVERSTATED = "contribution_overstated"
    AI_ASSISTED_UNEXPLAINED = "ai_assisted_unexplained"


class ReconcileChoice(StrEnum):
    """사실이 아닌 기여를 쓰는 선택지는 없다 (ADR-0017 정직성 원칙)."""

    REVISE_TO_FACT = "revise_to_fact"
    IMPLEMENT_THEN_REAUDIT = "implement_then_reaudit"
    ADD_EVIDENCE = "add_evidence"
    KEEP_AS_IS = "keep_as_is"  # fact → unverified, 리포트에 "근거와 다름", E7이 쓰지 않음
    LATER = "later"


class ReconciliationDecision(StrictModel):
    claim_id: str
    mismatch: Mismatch
    options_offered: list[ReconcileChoice] = Field(min_length=1)
    choice: ReconcileChoice | None = None
    revised_text: str | None = None

    @model_validator(mode="after")
    def _choice_offered(self) -> ReconciliationDecision:
        if self.choice is not None and self.choice not in self.options_offered:
            raise ValueError("choice must be one of options_offered")
        if self.choice is ReconcileChoice.REVISE_TO_FACT and not self.revised_text:
            raise ValueError("revise_to_fact needs revised_text")
        return self


class InterviewCard(StrictModel):
    claim_id: str
    likely_questions: list[str] = Field(min_length=1)
    evidence_locations: Citations
    talking_points: list[str] = Field(default_factory=list)
    caution: str | None = None
