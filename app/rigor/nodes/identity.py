"""A6: 커밋 신원 확인. GitHub 계정 이메일 밖의 별칭(다른 PC의 이메일 등)을 사용자가 고르게 한다.

추측으로 귀속하지 않는다. 후보를 보여 주고, 사용자가 고른 이메일만 본인 커밋으로 센다.
놓치면 기여가 실제보다 작게 잡힌다 (합성 케이스 D: 2/30 vs 4/30).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from rigor.schemas.profile import CommitIdentity

_SAFE = ["-c", "core.hooksPath=/dev/null"]


@dataclass(frozen=True)
class AuthorStat:
    name: str
    email: str
    commits: int


@dataclass(frozen=True)
class AliasCandidate:
    name: str
    email: str
    commits: int
    score: float
    reasons: tuple[str, ...]


def collect_authors(repo_paths: Iterable[Path]) -> list[AuthorStat]:
    counts: Counter[tuple[str, str]] = Counter()
    for path in repo_paths:
        out = subprocess.run(
            ["git", *_SAFE, "log", "--format=%an\t%ae"],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout
        for line in out.splitlines():
            name, _, email = line.partition("\t")
            counts[(name, email.lower())] += 1
    return [AuthorStat(n, e, c) for (n, e), c in counts.most_common()]


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9가-힣]+", s.lower()) if len(t) >= 2}


def propose_aliases(identity: CommitIdentity, authors: list[AuthorStat]) -> list[AliasCandidate]:
    """계정 이메일이 아닌 작성자를 모두 후보로 내고, 본인일 가능성 순으로 정렬한다."""
    known = {e.lower() for e in identity.emails}
    mine = _tokens(identity.github_login) | {t for a in identity.name_aliases for t in _tokens(a)}
    for e in known:
        mine |= _tokens(e.split("@")[0])
    candidates = []
    for a in authors:
        if a.email in known:
            continue
        reasons = []
        overlap = (_tokens(a.name) | _tokens(a.email.split("@")[0])) & mine
        if overlap:
            reasons.append(f"이름·이메일이 계정과 겹침: {', '.join(sorted(overlap))}")
        if (
            a.email.endswith("users.noreply.github.com")
            and identity.github_login.lower() in a.email
        ):
            reasons.append("GitHub noreply 이메일")
        score = min(1.0, 0.5 * len(reasons))
        candidates.append(AliasCandidate(a.name, a.email, a.commits, score, tuple(reasons)))
    return sorted(candidates, key=lambda c: (-c.score, -c.commits))


def confirm_aliases(
    identity: CommitIdentity,
    candidates: list[AliasCandidate],
    choose: Callable[[list[AliasCandidate]], list[str]],
) -> CommitIdentity:
    chosen = {e.lower() for e in choose(candidates)}
    offered = {c.email for c in candidates}
    if not chosen <= offered:
        raise ValueError("chosen emails must come from the offered candidates")
    emails = list(dict.fromkeys([*identity.emails, *sorted(chosen)]))
    return identity.model_copy(update={"emails": emails, "confirmed_by_user": True})
