"""C1: 결정론 신호. 셀 수 있는 것은 코드로 센다 (design.md §3). LLM을 쓰지 않는다."""

from __future__ import annotations

import re
import tomllib
from collections import Counter
from pathlib import PurePosixPath

from rigor.github_audit.snapshot import RepoSnapshot
from rigor.safety.injection import find_injection_lines
from rigor.schemas.profile import CommitIdentity
from rigor.schemas.repo import (
    CISignals,
    CommitSignals,
    DeploySignals,
    ReadmeSignals,
    RepoSignals,
    TestSignals,
)
from rigor.taxonomy import load_taxonomy

SECTION_KEYWORDS = {
    "problem": r"문제|배경|동기|개요|소개|problem|motivation|background|overview|why|about",
    "design": r"설계|아키텍처|구조|architecture|design|how it works|structure",
    "setup": r"실행|설치|사용법|시작|install|usage|setup|getting started|quick ?start|run",
    "results": r"결과|성능|평가|벤치마크|데모|results?|benchmarks?|evaluation|performance|demo",
}
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$"
)
_CI_PATH = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$|^\.gitlab-ci\.yml$|^\.circleci/")
_DEPLOY_FILES = re.compile(
    r"(^|/)(dockerfile|docker-compose[^/]*\.ya?ml|fly\.toml|vercel\.json|procfile|app\.ya?ml|"
    r"render\.ya?ml|netlify\.toml|serverless\.ya?ml)$",
    re.IGNORECASE,
)
_LANG = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rs": "Rust",
    ".ipynb": "Jupyter",
}
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([a-zA-Z_][\w]*)|import\s+([a-zA-Z_][\w]*))", re.MULTILINE)


def readme_path(snap: RepoSnapshot) -> str | None:
    for p in snap.paths:
        if "/" not in p and p.lower().startswith("readme"):
            return p
    return None


def readme_signals(snap: RepoSnapshot) -> ReadmeSignals:
    path = readme_path(snap)
    if not path or path not in snap.files:
        return ReadmeSignals(exists=bool(path))
    text = snap.files[path]
    headings = [h.lower() for h in re.findall(r"^#{1,4}\s+(.+)$", text, re.MULTILINE)]
    sections = [
        key
        for key, pattern in SECTION_KEYWORDS.items()
        if any(re.search(pattern, h, re.IGNORECASE) for h in headings)
    ]
    return ReadmeSignals(exists=True, length=len(text), sections=sections)


def dependencies(snap: RepoSnapshot) -> list[str]:
    """매니페스트 + 파이썬 import에서 기술 이름을 정규화해 모은다."""
    tax = load_taxonomy()
    raw: set[str] = set()
    for path, text in snap.files.items():
        name = PurePosixPath(path).name.lower()
        if name == "pyproject.toml":
            try:
                data = tomllib.loads(text)
            except tomllib.TOMLDecodeError:
                continue
            for dep in data.get("project", {}).get("dependencies", []):
                raw.add(re.split(r"[<>=!~\[; ]", dep, maxsplit=1)[0])
        elif name.startswith("requirements") and name.endswith(".txt"):
            for line in text.splitlines():
                line = line.split("#")[0].strip()
                if line and not line.startswith("-"):
                    raw.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0])
        elif name == "package.json":
            import json

            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            for key in ("dependencies", "devDependencies"):
                raw.update((data.get(key) or {}).keys())
        elif name.endswith(".py"):
            for a, b in _PY_IMPORT.findall(text):
                raw.add(a or b)
    return sorted({tax.normalize_skill(r) for r in raw if r})


def compute_signals(snap: RepoSnapshot, identity: CommitIdentity | None = None) -> RepoSignals:
    tests = [p for p in snap.paths if _TEST_PATH.search(p)]
    deps = dependencies(snap)
    frameworks = [f for f in ("pytest", "jest", "vitest", "unittest") if f in deps]
    ci = [p for p in snap.paths if _CI_PATH.search(p)]
    lic = next(
        (p for p in snap.paths if "/" not in p and p.lower().startswith(("license", "copying"))),
        None,
    )
    license_id = None
    if lic:
        head = snap.files.get(lic, "")[:200].lower()
        license_id = next(
            (k for k in ("MIT", "Apache", "GPL", "BSD", "MPL") if k.lower() in head), "other"
        )
    langs: Counter[str] = Counter()
    for p in snap.paths:
        ext = PurePosixPath(p).suffix.lower()
        if ext in _LANG:
            langs[_LANG[ext]] += len(snap.files.get(p, "")) or 1
    emails = {e.lower() for e in identity.emails} if identity else set()
    user_commits = [c for c in snap.commits if c.author_email in emails]
    weeks = {c.when.isocalendar()[:2] for c in user_commits}
    dates = sorted(c.when for c in snap.commits)
    injection = sum(len(find_injection_lines(t)) for t in snap.files.values())
    return RepoSignals(
        readme=readme_signals(snap),
        tests=TestSignals(exists=bool(tests), frameworks=frameworks, file_count=len(tests)),
        ci=CISignals(
            exists=bool(ci),
            providers=sorted({"github_actions" if "github" in p else "other" for p in ci}),
        ),
        license=license_id,
        deploy=DeploySignals(
            config_files=[p for p in snap.paths if _DEPLOY_FILES.search(p)],
            homepage_url=snap.meta.homepage,
        ),
        languages=dict(langs),
        dependencies=deps,
        commits=CommitSignals(
            total=len(snap.commits),
            user=len(user_commits),
            first_at=dates[0].date().isoformat() if dates else None,
            last_at=dates[-1].date().isoformat() if dates else None,
            active_weeks=len(weeks),
        ),
        size_kb=snap.size_kb,
        injection_suspects=injection,
    )
