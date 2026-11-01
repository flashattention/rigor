"""C2: 기여 분석. 판정 입력이 아니다 — 정합 선택지와 면접 준비 카드의 근거로만 쓴다.

커밋은 확인된 커밋 신원(A6: 계정 이메일 + 사용자가 고른 별칭)으로만 귀속한다.
커밋 수는 기여의 불완전한 지표이므로, 왜곡 가능성(caveats)을 항상 함께 낸다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from rigor.github_audit.markers import AGENT_BOT_NAMES, AI_COAUTHOR_MARKERS
from rigor.github_audit.snapshot import CommitInfo, RepoSnapshot
from rigor.schemas.profile import CommitIdentity
from rigor.schemas.repo import AreaContribution, ContributionAnalysis, ContributionCaveat

_SQUASH = re.compile(r"\(#\d+\)\s*$", re.MULTILINE)


def area_of(path: str, depth: int = 2) -> str:
    parts = PurePosixPath(path).parts
    if len(parts) <= 1:
        return "(root)"
    return "/".join(parts[: min(depth, len(parts) - 1)])


def is_ai_coauthored(c: CommitInfo) -> bool:
    for line in c.message.lower().splitlines():
        if line.startswith("co-authored-by:") and any(m in line for m in AI_COAUTHOR_MARKERS):
            return True
    return False


def analyze_contribution(snap: RepoSnapshot, identity: CommitIdentity) -> ContributionAnalysis:
    emails = {e.lower() for e in identity.emails}
    mine = [c for c in snap.commits if c.author_email in emails]
    user_changes: dict[str, int] = defaultdict(int)
    total_changes: dict[str, int] = defaultdict(int)
    for c in snap.commits:
        for ch in c.changes:
            n = ch.additions + ch.deletions or 1
            area = area_of(ch.path)
            total_changes[area] += n
            if c.author_email in emails:
                user_changes[area] += n
    caveats: list[ContributionCaveat] = []
    if not identity.confirmed_by_user:
        caveats.append(ContributionCaveat.UNMAPPED_EMAILS)
    if (
        snap.commits
        and sum(bool(_SQUASH.search(c.message)) for c in snap.commits) / len(snap.commits) > 0.3
    ):
        caveats.append(ContributionCaveat.SQUASH_MERGE)
    n_paths = max(len(snap.paths), 1)
    if any(len(c.changes) > 0.5 * n_paths and len(c.changes) > 20 for c in snap.commits):
        caveats.append(ContributionCaveat.BULK_MOVE)
    period = None
    if mine:
        dates = sorted(c.when for c in mine)
        period = (dates[0].date().isoformat(), dates[-1].date().isoformat())
    return ContributionAnalysis(
        repo_id=snap.meta.repo_id,
        user_commits=len(mine),
        total_commits=len(snap.commits),
        areas=sorted(
            (
                AreaContribution(path_prefix=a, user_changes=user_changes[a], total_changes=t)
                for a, t in total_changes.items()
            ),
            key=lambda x: -x.total_changes,
        ),
        active_period=period,
        ai_coauthored_commits=sum(is_ai_coauthored(c) for c in mine),
        agent_bot_prs=sum(
            any(b in c.author_name.lower() for b in AGENT_BOT_NAMES) for c in snap.commits
        ),
        caveats=caveats,
    )
