"""공고. docs/schemas.md §3. 연차는 knockout이 아니다 (ADR-0008 [B3])."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import Field, model_validator

from rigor.schemas.common import StrictModel


class RequirementKind(StrEnum):
    KNOCKOUT = "knockout"
    REQUIRED = "required"
    PREFERRED = "preferred"


class KnockoutType(StrEnum):
    DEGREE = "degree"
    CERTIFICATION = "certification"
    LOCATION = "location"
    WORK_AUTHORIZATION = "work_authorization"
    LANGUAGE_CERT = "language_cert"
    OTHER = "other"


class LegalCheck(StrEnum):
    OK = "ok"
    FLAG = "flag"  # 보호 속성 기반 요건 → 판정에서 제외하고 안내만


class Requirement(StrictModel):
    id: str = Field(pattern=r"^r_[A-Za-z0-9_]+$")
    text: str = Field(min_length=1)  # 원문 span
    kind: RequirementKind
    knockout_type: KnockoutType | None = None
    competencies: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    min_depth: int | None = Field(default=None, ge=0, le=3)
    legal_check: LegalCheck = LegalCheck.OK

    @model_validator(mode="after")
    def _knockout_type_only_for_knockout(self) -> Requirement:
        if self.kind is RequirementKind.KNOCKOUT and self.knockout_type is None:
            raise ValueError("knockout requirement needs knockout_type")
        if self.kind is not RequirementKind.KNOCKOUT and self.knockout_type is not None:
            raise ValueError("knockout_type is only valid for knockout requirements")
        return self

    @property
    def effective_min_depth(self) -> int:
        """min_depth가 없으면 required=2, preferred=1, knockout=0 (taxonomy §3)."""
        if self.min_depth is not None:
            return self.min_depth
        return {RequirementKind.REQUIRED: 2, RequirementKind.PREFERRED: 1}.get(self.kind, 0)


class SourceKind(StrEnum):
    USER_PASTE = "user_paste"
    PUBLIC_API = "public_api"
    PARTNER = "partner"
    ATS_BOARD = "ats_board"


class JobSource(StrictModel):
    kind: SourceKind = SourceKind.USER_PASTE
    name: str | None = None
    url: str | None = None
    collected_at: datetime | None = None
    terms_version: str | None = None


class ExperienceYears(StrictModel):
    min: float | None = Field(default=None, ge=0)
    max: float | None = Field(default=None, ge=0)
    strict: bool = False


class JobPosting(StrictModel):
    id: str
    owner_user_id: str | None = None  # ADR-0005: 붙여넣은 공고는 사용자 범위
    source: JobSource = Field(default_factory=JobSource)
    raw_text_hash: str | None = None
    company: str
    title: str
    role_ids: list[str] = Field(default_factory=list)
    language: str = "ko"
    blind: bool = False
    deadline: date | None = None
    experience_years: ExperienceYears = Field(default_factory=ExperienceYears)
    requirements: list[Requirement] = Field(min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_requirement_ids(self) -> JobPosting:
        ids = [r.id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement ids must be unique")
        return self

    def requirement(self, req_id: str) -> Requirement:
        for r in self.requirements:
            if r.id == req_id:
                return r
        raise KeyError(req_id)
