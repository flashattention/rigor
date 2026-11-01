"""RepoSpec → 실제 로컬 git 저장소. 작성자·날짜·트레일러를 명세대로 기록한다."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from rigor.synth.spec import RepoSpec

# 개발자 환경의 훅·서명 설정이 합성 커밋 생성에 끼어들지 않게 한다
_GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"]


def _git(args: list[str], cwd: Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(
        ["git", *_GIT_SAFE, *args],
        cwd=cwd,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", **(env or {})},
        check=True,
        capture_output=True,
    )


def materialize(repo: RepoSpec, root: Path) -> Path:
    """커밋 명세를 순서대로 실제 커밋으로 남긴다. 저장소 경로가 이미 있으면 FileExistsError."""
    path = root / repo.owner / repo.name
    path.mkdir(parents=True, exist_ok=False)
    _git(["init", "-q", "-b", "main"], path)
    for commit in repo.commits:
        for rel, content in commit.files.items():
            target = path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for rel in commit.deletes:
            (path / rel).unlink()
        author = repo.author(commit.author)
        when = commit.when.isoformat()
        message = commit.message
        if commit.trailers:
            message += "\n\n" + "\n".join(commit.trailers)
        _git(["add", "-A"], path)
        _git(
            ["commit", "-q", "--allow-empty", "-m", message],
            path,
            env={
                "GIT_AUTHOR_NAME": author.name,
                "GIT_AUTHOR_EMAIL": author.email,
                "GIT_AUTHOR_DATE": when,
                "GIT_COMMITTER_NAME": author.name,
                "GIT_COMMITTER_EMAIL": author.email,
                "GIT_COMMITTER_DATE": when,
            },
        )
    return path


def materialize_all(repos: list[RepoSpec], root: Path) -> dict[int, Path]:
    return {r.repo_id: materialize(r, root) for r in repos}
