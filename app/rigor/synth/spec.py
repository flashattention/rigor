"""합성 평가 케이스의 명세와 구성상 정답 (docs/metrics.md §0).

정답을 먼저 정하고 그에 맞게 데이터를 만든다. 정답은 LLM의 의견이 아니라 설계값이다.
실제 개인 이력은 쓰지 않는다. 이름·연락처·학교는 반사실 공정성 테스트를 위한 합성값이다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from rigor.schemas.diagnosis import Grade, VerdictStatus
from rigor.schemas.job import JobPosting
from rigor.schemas.profile import Profile, UnlinkedReason
from rigor.schemas.repo import ClaimResult, ExposureKind, Mismatch, RepoVerdict

AI_COAUTHOR_MARKERS = ("noreply@anthropic.com", "copilot", "cursor", "codex", "openai")


class AuthorSpec(BaseModel):
    key: str
    name: str
    email: str
    is_user: bool = False


class CommitSpec(BaseModel):
    author: str  # AuthorSpec.key
    message: str
    when: datetime
    files: dict[str, str] = Field(default_factory=dict)
    deletes: list[str] = Field(default_factory=list)
    trailers: list[str] = Field(default_factory=list)  # 예: "Co-Authored-By: Claude <...>"

    @property
    def ai_coauthored(self) -> bool:
        return any(
            t.lower().startswith("co-authored-by:")
            and any(m in t.lower() for m in AI_COAUTHOR_MARKERS)
            for t in self.trailers
        )


class RepoSpec(BaseModel):
    repo_id: int
    owner: str
    name: str
    description: str = ""
    topics: list[str] = Field(default_factory=list)
    is_fork: bool = False
    authors: list[AuthorSpec]
    commits: list[CommitSpec] = Field(min_length=1)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"

    def author(self, key: str) -> AuthorSpec:
        return next(a for a in self.authors if a.key == key)

    def final_files(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for c in self.commits:
            files.update(c.files)
            for path in c.deletes:
                files.pop(path, None)
        return files

    def user_commit_count(self) -> int:
        return sum(1 for c in self.commits if self.author(c.author).is_user)


class PersonaHeader(BaseModel):
    """이력서 상단에만 렌더링된다. 프로필 fact에는 들어가지 않는다 (ADR-0014)."""

    name: str
    email: str
    phone: str
    github_login: str
    birth_year: int | None = None
    gender_cue: str | None = None  # 예: "남" / "여" (반사실 테스트용)
    school: str


class ClaimTruth(BaseModel):
    claim_id: str
    result: ClaimResult
    mismatch: Mismatch | None = None
    keyword: str | None = None  # tech claim이면 저장소에 있어야/없어야 할 의존성 이름


class UnlinkedTruth(BaseModel):
    fact_id: str
    reason: UnlinkedReason
    correct_repo_id: int | None = None  # reason=forgot일 때 후보 추천의 정답


class ExposureTruth(BaseModel):
    repo_id: int
    kind: ExposureKind
    path: str


class RepoTruth(BaseModel):
    repo_id: int
    verdict: RepoVerdict
    user_share: float = Field(ge=0, le=1)  # 확인된 모든 커밋 신원 기준
    user_share_without_aliases: float | None = None  # GitHub 계정 이메일만 썼을 때
    ai_coauthored_commits: int = 0


class GroundTruth(BaseModel):
    verdicts: dict[str, VerdictStatus]
    critical_gap_requirements: list[str] = Field(default_factory=list)
    grade: Grade
    redirect_role: str | None = None
    alternative_expected: bool = False
    claims: list[ClaimTruth] = Field(default_factory=list)
    unlinked: list[UnlinkedTruth] = Field(default_factory=list)
    exposures: list[ExposureTruth] = Field(default_factory=list)
    repos: list[RepoTruth] = Field(default_factory=list)


class Case(BaseModel):
    case_id: str
    description: str
    tags: list[str] = Field(default_factory=list)
    header: PersonaHeader
    profile: Profile
    job: JobPosting
    repos: list[RepoSpec]
    linked_in_resume: dict[str, int] = Field(default_factory=dict)  # fact_id → repo_id
    truth: GroundTruth
    resume_extra_lines: list[str] = Field(default_factory=list)  # 인젝션 변형용
    jd_extra_lines: list[str] = Field(default_factory=list)

    def repo(self, repo_id: int) -> RepoSpec:
        return next(r for r in self.repos if r.repo_id == repo_id)
