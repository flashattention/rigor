"""B단계: 이력서 항목 ↔ 저장소 연결 (ADR-0017 B1~B4).

B1 링크 검증 → B2 미연결 항목의 후보 저장소 추천 → B3 사용자가 사유 선택 → B4 규칙으로 처리.
"저장소가 없으면 빼라"는 개인 프로젝트에만 적용한다. 회사 업무·연구는 저장소가 원래 없다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from rigor.github_audit.snapshot import RepoMeta
from rigor.schemas.profile import Fact, FactCategory, Profile, RepoLink, UnlinkedReason
from rigor.schemas.repo import RepoCandidate, UnlinkedItem, UnlinkedProposal
from rigor.taxonomy import load_taxonomy

LINKABLE = (FactCategory.PROJECT, FactCategory.EXPERIENCE)
MIN_CANDIDATE_SCORE = 0.25
W_TEXT, W_SKILL, W_TIME = 0.45, 0.35, 0.20


class LinkStatus(StrEnum):
    OK = "ok"
    NOT_IN_ACCOUNT = (
        "not_in_account"  # 사용자 소유도 기여도 아닌 저장소 → 분석하지 않음 (본인 계정만)
    )
    FORK_WITHOUT_CHANGES = "fork_without_changes"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class LinkCheck:
    fact_id: str
    url: str
    status: LinkStatus
    repo_id: int | None = None


@dataclass(frozen=True)
class RepoFacts:
    """후보 추천에 쓰는 저장소 요약 (신호 계산 결과에서 만든다)."""

    meta: RepoMeta
    dependencies: tuple[str, ...] = ()
    user_period: tuple[date, date] | None = None
    user_commits: int = 0


# 저장소 루트 URL만 받는다. /tree/main 같은 하위 경로는 malformed가 된다.
_URL = re.compile(r"github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def check_links(profile: Profile, catalog: list[RepoFacts]) -> list[LinkCheck]:
    """B1. 카탈로그 = 사용자 소유 저장소 + 사용자 커밋이 있는 저장소."""
    by_name = {r.meta.full_name.lower(): r for r in catalog}
    out = []
    for f in profile.facts:
        if not (f.repo_link and f.repo_link.url):
            continue
        m = _URL.search(f.repo_link.url)
        if not m:
            out.append(LinkCheck(f.id, f.repo_link.url, LinkStatus.MALFORMED))
            continue
        repo = by_name.get(m.group(1).lower())
        if repo is None:
            out.append(LinkCheck(f.id, f.repo_link.url, LinkStatus.NOT_IN_ACCOUNT))
        elif repo.meta.is_fork and repo.user_commits == 0:
            out.append(
                LinkCheck(f.id, f.repo_link.url, LinkStatus.FORK_WITHOUT_CHANGES, repo.meta.repo_id)
            )
        else:
            out.append(LinkCheck(f.id, f.repo_link.url, LinkStatus.OK, repo.meta.repo_id))
    return out


def apply_link_checks(profile: Profile, checks: list[LinkCheck]) -> Profile:
    """카탈로그에서 찾은 링크에 repo_id를 채운다. 변경 없는 포크도 연결되어 E4의 X3에서 걸러진다."""
    by_fact = {c.fact_id: c for c in checks}
    facts = []
    for f in profile.facts:
        c = by_fact.get(f.id)
        if c and c.repo_id is not None:
            f = f.model_copy(update={"repo_link": RepoLink(repo_id=c.repo_id, url=c.url)})
        facts.append(f)
    return profile.model_copy(update={"facts": facts})


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9a-z가-힣]+", text.lower()) if len(t) >= 2}


def _fact_skills(f: Fact) -> set[str]:
    tax = load_taxonomy()
    return {tax.normalize_skill(s) for s in f.skills}


def score_candidate(f: Fact, repo: RepoFacts) -> RepoCandidate:
    """이름·설명, 기술, 활동 기간 겹침의 가중합과 사람이 읽을 이유를 낸다."""
    fact_text = _tokens(" ".join([f.content, f.role or "", *[c.text for c in f.claims]]))
    repo_text = _tokens(
        " ".join([repo.meta.name.replace("-", " "), repo.meta.description, *repo.meta.topics])
    )
    text = len(fact_text & repo_text) / max(1, min(len(fact_text), len(repo_text)))
    skills = _fact_skills(f)
    skill = len(skills & set(repo.dependencies)) / len(skills) if skills else 0.0
    time = 0.0
    if repo.user_period and (f.period.start or f.period.end):
        start, end = f.period.start or date.min, f.period.end or date.max
        first, last = repo.user_period
        time = 1.0 if not (last < start or first > end) else 0.0
    reasons = []
    if text:
        reasons.append(f"이름·설명 겹침: {', '.join(sorted(fact_text & repo_text)[:4])}")
    if skill:
        reasons.append(f"기술 겹침: {', '.join(sorted(skills & set(repo.dependencies)))}")
    if time:
        reasons.append("활동 기간이 이력서 기간과 겹침")
    score = round(W_TEXT * min(text, 1.0) + W_SKILL * skill + W_TIME * time, 3)
    return RepoCandidate(
        repo_id=repo.meta.repo_id, full_name=repo.meta.full_name, score=score, reasons=reasons
    )


def find_unlinked(profile: Profile, catalog: list[RepoFacts]) -> list[UnlinkedItem]:
    """B2. 저장소가 안 적힌 프로젝트·경력 항목마다 후보 상위 3개 (점수 하한 미만은 제외)."""
    linked = {f.repo_link.repo_id for f in profile.facts if f.repo_link and f.repo_link.repo_id}
    items = []
    for f in profile.facts:
        if f.category not in LINKABLE or (f.repo_link and f.repo_link.repo_id):
            continue
        cands = [score_candidate(f, r) for r in catalog if r.meta.repo_id not in linked]
        cands = sorted((c for c in cands if c.score >= MIN_CANDIDATE_SCORE), key=lambda c: -c.score)
        items.append(UnlinkedItem(fact_id=f.id, candidates=cands[:3]))
    return items


def propose(fact: Fact, reason: UnlinkedReason, chosen_repo_id: int | None) -> UnlinkedProposal:
    """B4 처리 규칙 (ADR-0017 표)."""
    if reason is UnlinkedReason.FORGOT:
        if chosen_repo_id is None:
            raise ValueError("forgot requires choosing a repository")
        return UnlinkedProposal.LINK
    if reason is UnlinkedReason.LOW_QUALITY:
        return UnlinkedProposal.PRIVATE_AUDIT
    if reason is UnlinkedReason.PRIVATE_OR_COMPANY:
        return UnlinkedProposal.KEEP_WITH_INTERVIEW_PREP
    if fact.category is FactCategory.PROJECT:
        return UnlinkedProposal.ADD_EVIDENCE_OR_REDUCE
    return UnlinkedProposal.KEEP_WITH_INTERVIEW_PREP


Chooser = Callable[[Fact, UnlinkedItem], tuple[UnlinkedReason, int | None]]


def resolve_unlinked(
    profile: Profile, items: list[UnlinkedItem], catalog: list[RepoFacts], choose: Chooser
) -> tuple[Profile, list[UnlinkedItem]]:
    """B3+B4. 사용자 선택을 받아 연결하거나 처리 제안을 붙인다."""
    by_id = {r.meta.repo_id: r for r in catalog}
    facts = {f.id: f for f in profile.facts}
    resolved = []
    for item in items:
        fact = facts[item.fact_id]
        reason, repo_id = choose(fact, item)
        if repo_id is not None and repo_id not in by_id:
            raise ValueError("chosen repository is not in the user's catalog")
        proposal = propose(fact, reason, repo_id)
        link = RepoLink(unlinked_reason=reason)
        if repo_id is not None and proposal in (
            UnlinkedProposal.LINK,
            UnlinkedProposal.PRIVATE_AUDIT,
        ):
            link = RepoLink(repo_id=repo_id, url=by_id[repo_id].meta.url, unlinked_reason=reason)
        facts[item.fact_id] = fact.model_copy(update={"repo_link": link})
        resolved.append(
            item.model_copy(
                update={"user_reason": reason, "chosen_repo_id": repo_id, "proposal": proposal}
            )
        )
    return profile.model_copy(update={"facts": [facts[f.id] for f in profile.facts]}), resolved
