"""저장소 스냅샷: 로컬 git과 GitHub API가 같은 형태로 돌려준다.

신호·기여·claim 검증 코드는 데이터가 어디서 왔는지 모른다. 그래서 합성 저장소(로컬)로
테스트한 로직이 실제 GitHub 저장소에도 그대로 적용된다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class RepoMeta:
    repo_id: int
    full_name: str
    description: str = ""
    topics: tuple[str, ...] = ()
    is_fork: bool = False
    homepage: str | None = None
    visibility: Literal["public", "private"] = "public"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}"

    @property
    def name(self) -> str:
        return self.full_name.split("/", 1)[1]


@dataclass(frozen=True)
class FileChange:
    path: str
    additions: int
    deletions: int


@dataclass(frozen=True)
class CommitInfo:
    """커밋 하나. author_email은 소스가 소문자로 정규화해 넣는다."""

    sha: str
    author_name: str
    author_email: str
    when: datetime
    message: str
    changes: tuple[FileChange, ...] = ()  # API 스냅샷에서는 비어 있을 수 있다
    author_login: str | None = None  # GitHub 계정에 연결된 커밋이면 그 로그인 (API 스냅샷)


@dataclass
class RepoSnapshot:
    meta: RepoMeta
    head_sha: str
    paths: list[str]  # 전체 파일 경로
    files: dict[str, str]  # 내용을 가져온 파일 (일부일 수 있다)
    commits: list[CommitInfo]
    size_kb: int = 0
    scope: Literal["local", "api_partial", "full_clone"] = "local"
    notes: list[str] = field(default_factory=list)

    @property
    def has_line_stats(self) -> bool:
        return any(c.changes for c in self.commits)
