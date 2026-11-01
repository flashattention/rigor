"""로컬 git 저장소 → RepoSnapshot. 코드는 읽기만 하고 실행하지 않는다 (ADR-0006).

합성 평가 저장소와, M2의 격리 clone 결과에 같은 경로로 쓰인다.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path

from rigor.github_audit.snapshot import CommitInfo, FileChange, RepoMeta, RepoSnapshot

MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 3_000_000
# 분석 대상 저장소의 훅과 fsmonitor 설정이 명령을 실행하지 못하게 막는다
_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
_SEP = "\x1e"  # 커밋 구분자
_FIELD = "\x1f"


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *_SAFE, *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
    ).stdout


def _commits(path: Path) -> list[CommitInfo]:
    """git log와 numstat을 파싱한다. 구분자가 제어 문자라 커밋 메시지 내용과 섞이지 않는다."""
    fmt = f"{_SEP}%H{_FIELD}%an{_FIELD}%ae{_FIELD}%aI{_FIELD}%B{_FIELD}"
    raw = _git(path, "log", f"--format={fmt}", "--numstat", "--no-renames")
    commits = []
    for block in raw.split(_SEP)[1:]:
        sha, name, email, when, message, stats = block.split(_FIELD, 5)
        changes = []
        for line in stats.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                add, rem, file = parts
                changes.append(
                    FileChange(
                        file, int(add) if add.isdigit() else 0, int(rem) if rem.isdigit() else 0
                    )
                )
        commits.append(
            CommitInfo(
                sha=sha,
                author_name=name,
                author_email=email.lower(),
                when=datetime.fromisoformat(when),
                message=message.strip(),
                changes=tuple(changes),
            )
        )
    return commits


def snapshot_local(path: Path, meta: RepoMeta) -> RepoSnapshot:
    """git이 추적하는 파일을 작업 트리에서 읽는다.

    심볼릭 링크, 상한 초과, UTF-8이 아닌 파일은 paths에 남고 files에서만 빠진다.
    """
    head = _git(path, "rev-parse", "HEAD").strip()
    paths = [p for p in _git(path, "ls-files", "-z").split("\0") if p]
    files: dict[str, str] = {}
    total = 0
    size = 0
    for rel in paths:
        file = path / rel
        if file.is_symlink() or not file.is_file():
            continue  # 심볼릭 링크는 따라가지 않는다
        n = file.stat().st_size
        size += n
        if n > MAX_FILE_BYTES or total + n > MAX_TOTAL_BYTES:
            continue
        try:
            files[rel] = file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        total += n
    return RepoSnapshot(
        meta=meta,
        head_sha=head,
        paths=paths,
        files=files,
        commits=_commits(path),
        size_kb=size // 1024,
        scope="local",
    )
