"""평가용 공개 저장소를 정해진 규칙으로 고른다 (docs/evals-agent-design.md).

결과를 보고 고르지 않기 위해 기준과 순서를 코드로 고정한다. 조정용/측정용은 이름의 해시로 나눈다.
결과는 저장소 이름을 공개하지 않도록 git에서 제외된 data/private/evals/에만 저장한다.
코드는 받지 않는다: 메타데이터와 파일 목록만 본다.

    GITHUB_TOKEN=$(gh auth token) uv run python evals/agent/select_repos.py
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

from rigor.github_audit.api_source import API, GitHubError, default_fetch

LANGUAGES = {"Python": (".py",), "TypeScript": (".ts", ".tsx")}
PER_LANGUAGE = 6
MIN_CODE_FILES, MAX_CODE_FILES = 20, 300
QUERY = "language:{lang} license:mit license:apache-2.0 license:bsd-3-clause stars:50..500 pushed:>2026-01-01 fork:false archived:false"
_EXCLUDE_NAME = re.compile(
    r"awesome|tutorial|course|example|sample|demo|book|learn|cheatsheet|template|boilerplate|starter|dotfiles|interview|roadmap",
    re.I,
)
OUT = Path("data/private/evals/repos.json")


def eligible(meta: dict, paths: list[str], extensions: tuple[str, ...], exclude_owner: str) -> bool:
    if meta.get("fork") or meta.get("archived") or meta["owner"]["login"] == exclude_owner:
        return False
    if _EXCLUDE_NAME.search(meta["name"]) or _EXCLUDE_NAME.search(meta.get("description") or ""):
        return False
    code = [p for p in paths if p.endswith(extensions)]
    return MIN_CODE_FILES <= len(code) <= MAX_CODE_FILES


def split_of(full_name: str, index_in_language: int) -> str:
    """언어마다 번갈아 나눈다. 순서는 이름의 해시로 정하므로 내용을 보고 고르지 않는다."""
    return "dev" if index_in_language % 2 == 0 else "test"


def order_key(full_name: str) -> str:
    return hashlib.sha256(full_name.encode()).hexdigest()


def select(fetch=default_fetch, exclude_owner: str = "flashattention") -> list[dict]:
    chosen: list[dict] = []
    for lang, extensions in LANGUAGES.items():
        found: list[dict] = []
        for page in range(1, 6):
            url = f"{API}/search/repositories?q={quote(QUERY.format(lang=lang))}&sort=stars&order=desc&per_page=50&page={page}"
            for meta in json.loads(fetch(url))["items"]:
                full = meta["full_name"]
                branch = meta["default_branch"]
                try:
                    sha = json.loads(fetch(f"{API}/repos/{full}/commits/{quote(branch)}"))["sha"]
                    tree = json.loads(fetch(f"{API}/repos/{full}/git/trees/{sha}?recursive=1"))
                except GitHubError:
                    continue
                if tree.get("truncated"):
                    continue
                paths = [t["path"] for t in tree["tree"] if t["type"] == "blob"]
                if eligible(meta, paths, extensions, exclude_owner):
                    found.append(
                        {
                            "full_name": full,
                            "sha": sha,
                            "language": lang,
                            "code_files": sum(p.endswith(extensions) for p in paths),
                        }
                    )
                if len(found) == PER_LANGUAGE:
                    break
            if len(found) == PER_LANGUAGE:
                break
        for i, repo in enumerate(sorted(found, key=lambda r: order_key(r["full_name"]))):
            chosen.append({**repo, "split": split_of(repo["full_name"], i)})
    return chosen


if __name__ == "__main__":
    repos = select()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(repos, ensure_ascii=False, indent=2), encoding="utf-8")
    for split in ("dev", "test"):
        print(
            split,
            sum(r["split"] == split for r in repos),
            [r["language"] for r in repos if r["split"] == split],
        )
    sys.exit(0 if len(repos) == PER_LANGUAGE * len(LANGUAGES) else 1)
