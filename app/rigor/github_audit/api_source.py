"""공개 GitHub REST API → RepoSnapshot (ADR-0006: API 우선, clone은 필요할 때만).

- 코드는 실행하지 않는다. 텍스트만 가져온다.
- 파일 내용은 판단에 필요한 것만 상한 안에서 가져온다 (README, 매니페스트, CI, 라이선스,
  결과 문서, 소스 일부). 그래서 scope="api_partial"이고 리포트에 스캔 범위를 밝힌다.
- 줄 단위 변경 통계는 가져오지 않는다(커밋마다 요청이 필요해 한도를 크게 쓴다) → 영역별 기여는
  M2의 격리 clone에서.
- 테스트는 fetch 함수를 주입해 네트워크 없이 돈다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

from rigor.github_audit.snapshot import CommitInfo, RepoMeta, RepoSnapshot

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_COMMITS = 300
MAX_SOURCE_FILES = 30
MAX_FILE_BYTES = 50_000
MAX_TOTAL_BYTES = 400_000

Fetch = Callable[[str], bytes]

_ALWAYS = re.compile(
    r"^(readme[^/]*|license[^/]*|copying[^/]*|pyproject\.toml|setup\.py|setup\.cfg|"
    r"requirements[^/]*\.txt|package\.json|go\.mod|cargo\.toml|dockerfile|docker-compose[^/]*|"
    r"\.github/workflows/[^/]+\.ya?ml|\.gitlab-ci\.yml)$",
    re.IGNORECASE,
)
_RESULTS = re.compile(
    r"(^|/)(eval|evals|results?|benchmarks?|docs)/[^/]+\.(md|csv|json)$|benchmark", re.I
)
_SOURCE = re.compile(r"\.(py|ts|tsx|js|go|java|kt|rs)$")


class GitHubError(RuntimeError):
    pass


def default_fetch(url: str) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "rigor-audit"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise GitHubError("GitHub API 요청 한도 초과. 잠시 후 다시 시도하세요.") from exc
        if exc.code == 404:
            raise GitHubError(f"찾을 수 없음: {url}") from exc
        raise GitHubError(f"GitHub API 오류 {exc.code}: {url}") from exc


class GitHubSource:
    def __init__(self, fetch: Fetch = default_fetch):
        self.fetch = fetch

    def _json(self, url: str):
        return json.loads(self.fetch(url))

    def meta(self, full_name: str) -> tuple[RepoMeta, dict]:
        data = self._json(f"{API}/repos/{full_name}")
        meta = RepoMeta(
            repo_id=int(data["id"]),
            full_name=data["full_name"],
            description=data.get("description") or "",
            topics=tuple(data.get("topics") or ()),
            is_fork=bool(data.get("fork")),
            homepage=data.get("homepage") or None,
            visibility="private" if data.get("private") else "public",
        )
        return meta, data

    def list_user_repos(self, login: str) -> list[RepoMeta]:
        data = self._json(f"{API}/users/{quote(login)}/repos?per_page=100&type=owner&sort=pushed")
        return [
            RepoMeta(
                repo_id=int(d["id"]),
                full_name=d["full_name"],
                description=d.get("description") or "",
                topics=tuple(d.get("topics") or ()),
                is_fork=bool(d.get("fork")),
                homepage=d.get("homepage") or None,
            )
            for d in data
        ]

    def is_contributor(self, full_name: str, login: str) -> bool:
        """본인 소유가 아닌 저장소(팀 조직, 다른 사용자)에 본인 계정의 커밋이 있는가 (ADR-0006)."""
        try:
            return bool(
                self._json(f"{API}/repos/{full_name}/commits?author={quote(login)}&per_page=1")
            )
        except GitHubError:
            return False

    def snapshot(self, full_name: str) -> RepoSnapshot:
        meta, raw = self.meta(full_name)
        branch = raw.get("default_branch") or "main"
        head = self._json(f"{API}/repos/{full_name}/commits/{quote(branch)}")["sha"]
        tree = self._json(f"{API}/repos/{full_name}/git/trees/{head}?recursive=1")
        blobs = [
            (t["path"], int(t.get("size") or 0))
            for t in tree.get("tree", [])
            if t.get("type") == "blob"
        ]
        notes = ["파일 목록이 잘림 (매우 큰 저장소)"] if tree.get("truncated") else []

        selected = [
            p for p, s in blobs if (_ALWAYS.match(p) or _RESULTS.search(p)) and s <= MAX_FILE_BYTES
        ]
        sources = sorted((s, p) for p, s in blobs if _SOURCE.search(p) and s <= MAX_FILE_BYTES)
        selected += [p for _, p in sources[:MAX_SOURCE_FILES] if p not in selected]
        files: dict[str, str] = {}
        total = 0
        for path in selected:
            try:
                body = self.fetch(f"{RAW}/{full_name}/{head}/{quote(path)}")
            except GitHubError:
                continue
            if total + len(body) > MAX_TOTAL_BYTES:
                notes.append("파일 내용 상한 도달, 일부만 분석")
                break
            try:
                files[path] = body.decode("utf-8")
            except UnicodeDecodeError:
                continue
            total += len(body)

        commits: list[CommitInfo] = []
        page = 1
        while len(commits) < MAX_COMMITS:
            batch = self._json(
                f"{API}/repos/{full_name}/commits?sha={head}&per_page=100&page={page}"
            )
            if not batch:
                break
            for c in batch:
                author = c["commit"]["author"]
                account = c.get("author") or {}
                commits.append(
                    CommitInfo(
                        sha=c["sha"],
                        author_name=author.get("name") or "",
                        author_email=(author.get("email") or "").lower(),
                        when=datetime.fromisoformat(author["date"].replace("Z", "+00:00")),
                        message=c["commit"].get("message") or "",
                        author_login=account.get("login"),
                    )
                )
            if len(batch) < 100:
                break
            page += 1
        return RepoSnapshot(
            meta=meta,
            head_sha=head,
            paths=[p for p, _ in blobs],
            files=files,
            commits=commits,
            size_kb=int(raw.get("size") or 0),
            scope="api_partial",
            notes=notes,
        )
