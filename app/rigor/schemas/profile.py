"""지원자 프로필(사실 DB). docs/schemas.md §2, §4.5~4.6.

보호 속성(사진, 생년월일, 성별, 주소, 병역, 장애, 가족, 종교 등)은 필드 자체가 없다 (ADR-0014).
학력은 정규화 값만 판정에 쓰고, 학교명은 organization에만 남아 LLM에는 마스킹되어 전달된다.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from rigor.schemas.common import StrictModel


class FactCategory(StrEnum):
    EXPERIENCE = "experience"
    PROJECT = "project"
    EDUCATION = "education"
    AWARD = "award"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    SKILL = "skill"


# 성과가 본인 기여인지 팀 성과인지 구분한다.
class Attribution(StrEnum):
    INDIVIDUAL = "individual"
    TEAM = "team"
    MIXED = "mixed"


class Implementation(StrEnum):
    """자기 신고한 구현 방식. 등급 입력이 아니라 정합 선택지와 면접 준비 카드에만 쓴다."""

    HAND_WRITTEN = "hand_written"
    AI_ASSISTED = "ai_assisted"
    MOSTLY_AI = "mostly_ai"
    UNKNOWN = "unknown"


class FactStatus(StrEnum):
    """사용자 확인·정합 결과. 판정 근거로는 confirmed와 revised만 쓴다."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    REVISED = "revised"  # 정합(D)에서 사실에 맞게 고침
    UNVERIFIED = "unverified"  # 저장소와 불일치했고 고치지 않음 / "나중에"
    REJECTED = "rejected"

    @property
    def usable_as_evidence(self) -> bool:
        return self in (FactStatus.CONFIRMED, FactStatus.REVISED)


class ClaimType(StrEnum):
    TECH = "tech"
    FEATURE = "feature"
    METRIC = "metric"
    ROLE = "role"
    PERIOD = "period"
    LINK = "link"


class UnlinkedReason(StrEnum):
    """이력서 항목에 저장소 링크가 없는 이유. 사용자가 고른다."""

    NO_REPO = "no_repo"
    LOW_QUALITY = "low_quality"
    FORGOT = "forgot"
    PRIVATE_OR_COMPANY = "private_or_company"


class Degree(StrEnum):
    NONE = "none"
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    PHD = "phd"


class EducationStatus(StrEnum):
    ENROLLED = "enrolled"
    GRADUATED = "graduated"
    EXPECTED = "expected"
    DROPPED = "dropped"


class Period(StrictModel):
    start: date | None = None
    end: date | None = None
    ongoing: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.start and self.end and self.end < self.start:
            raise ValueError("period.end must not precede period.start")
        return self


class Metric(StrictModel):
    name: str
    value: float
    unit: str
    evidence_url: str | None = None


# 학력 판정에는 이 값만 쓴다. 학교명은 Fact.organization에만 남는다.
class EducationNormalized(StrictModel):
    degree: Degree
    field: str | None = None
    status: EducationStatus


class RepoLink(StrictModel):
    """저장소 연결(B) 결과. unlinked_reason은 링크가 없던 이유로, 후보를 골라 연결해도 남는다."""

    repo_id: int | None = None
    url: str | None = None
    unlinked_reason: UnlinkedReason | None = None


class Claim(StrictModel):
    """fact에서 뽑은 원자 주장. 저장소와 대조하는 단위다."""

    id: str = Field(pattern=r"^c_[A-Za-z0-9_]+$")
    fact_id: str
    type: ClaimType
    text: str = Field(min_length=1)
    question: str | None = None  # 검증 가능한 질문으로 바꾼 것
    repo_id: int | None = None


class Fact(StrictModel):
    """프로필의 사실 1건. claims는 모두 이 fact 소속이어야 한다."""

    id: str = Field(pattern=r"^f_[A-Za-z0-9_]+$")
    category: FactCategory
    content: str = Field(min_length=1)
    period: Period = Field(default_factory=Period)
    organization: str | None = None
    education_normalized: EducationNormalized | None = None
    role: str | None = None
    attribution: Attribution = Attribution.INDIVIDUAL
    contribution_note: str | None = None  # 사용자 확인값만. 추정 금지
    implementation: Implementation = Implementation.UNKNOWN
    skills: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    evidence_links: list[str] = Field(default_factory=list)
    repo_link: RepoLink | None = None
    disclosable_in_blind: bool = True
    status: FactStatus = FactStatus.UNCONFIRMED
    source: str = "resume_extract"
    claims: list[Claim] = Field(default_factory=list)

    @model_validator(mode="after")
    def _claims_belong_to_fact(self) -> Fact:
        for claim in self.claims:
            if claim.fact_id != self.id:
                raise ValueError(f"claim {claim.id} belongs to {claim.fact_id}, not {self.id}")
        return self


class CommitIdentity(StrictModel):
    """어떤 커밋을 사용자 것으로 셀지 정하는 기준 (A6)."""

    github_login: str
    emails: list[str] = Field(default_factory=list)
    name_aliases: list[str] = Field(default_factory=list)
    confirmed_by_user: bool = False


class Profile(StrictModel):
    id: str
    target_roles: list[str] = Field(default_factory=list)
    available_hours_per_week: int | None = Field(default=None, ge=0, le=168)
    deadline: date | None = None
    identity: CommitIdentity | None = None
    facts: list[Fact] = Field(default_factory=list)

    def evidence_facts(self) -> list[Fact]:
        """판정 근거로 쓸 수 있는 fact만 (confirmed, revised)."""
        return [f for f in self.facts if f.status.usable_as_evidence]
