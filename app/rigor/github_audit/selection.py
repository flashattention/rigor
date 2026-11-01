"""어떤 저장소를 깊게 수집할지 고른다 (요청 한도·시간 절약).

계정에 저장소가 수십 개여도 전부 깊게 받지 않는다:
1. 이력서에 링크된 저장소 — 본인 소유이거나, 본인 계정의 커밋이 있는 저장소만 (본인 계정만 분석, ADR-0006)
2. 저장소가 안 적힌 항목마다 메타데이터(이름·설명·주제)로 1차 선별한 상위 후보
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rigor.github_audit.snapshot import RepoMeta, RepoSnapshot
from rigor.nodes.repo_linking import LINKABLE, RepoFacts, score_candidate
from rigor.schemas.profile import CommitIdentity, Profile

CANDIDATES_PER_FACT = 3
_URL = re.compile(r"github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


@dataclass
class Selection:
    full_names: list[str]
    skipped_not_mine: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def select_repos(profile: Profile, owned: list[RepoMeta], login: str, is_contributor) -> Selection:
    owned_names = {m.full_name.lower(): m.full_name for m in owned}
    chosen: dict[str, None] = {}
    skipped = []
    for f in profile.facts:
        if not (f.repo_link and f.repo_link.url):
            continue
        m = _URL.search(f.repo_link.url)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in owned_names:
            chosen[owned_names[name.lower()]] = None
        elif is_contributor(name, login):
            chosen[name] = None
        else:
            skipped.append(name)
    linked_names = {n.lower() for n in chosen}
    pool = [RepoFacts(meta=m) for m in owned if m.full_name.lower() not in linked_names]
    for f in profile.facts:
        if f.category not in LINKABLE or (f.repo_link and f.repo_link.url):
            continue
        scored = sorted((score_candidate(f, r) for r in pool), key=lambda c: -c.score)
        for c in scored[:CANDIDATES_PER_FACT]:
            if c.score > 0:
                chosen[c.full_name] = None
    notes = [
        f"후보 탐색은 메타데이터 1차 선별 상위 {CANDIDATES_PER_FACT}개/항목 (전체 {len(owned)}개 중)"
    ]
    return Selection(list(chosen), skipped, notes)


def identity_from_account(
    login: str, snapshots: dict[int, RepoSnapshot], extra_emails: list[str] = ()
) -> CommitIdentity:
    """GitHub 계정에 연결된 커밋의 이메일 = 확인된 신원. 그 밖의 별칭은 사용자가 추가한다 (A6)."""
    emails = {
        c.author_email
        for s in snapshots.values()
        for c in s.commits
        if c.author_login == login and c.author_email
    }
    emails |= {e.lower() for e in extra_emails}
    return CommitIdentity(github_login=login, emails=sorted(emails), confirmed_by_user=True)
