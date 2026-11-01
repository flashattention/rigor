"""D단계: 정합 (ADR-0017). 사실을 먼저 맞추고 나서 판단한다.

D1 불일치마다 선택지를 주고 사용자가 고른다 — 사실이 아닌 기여를 쓰는 선택지는 없다.
D2 선택을 반영해 "판정 근거로 쓸 프로필"을 만든다. 고치지 않은 불일치 claim은 근거에서 빠진다.
D3 면접 준비 카드 (LLM). 카드의 인용은 규칙이 붙인다 (LLM이 근거 id를 지어내지 못하게).

수정 문장 제안은 LLM이 아니라 규칙 템플릿으로 만든다 — 규칙은 지어낼 수 없다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import Field

from rigor.github_audit.claims import skills_in_text
from rigor.llm.client import LLMClient
from rigor.prompts import data_block, load_prompt
from rigor.schemas.common import Citation, DraftModel
from rigor.schemas.profile import (
    Attribution,
    Claim,
    ClaimType,
    Fact,
    FactCategory,
    FactStatus,
    Implementation,
    Profile,
)
from rigor.schemas.repo import (
    ClaimCheck,
    ClaimResult,
    ContributionAnalysis,
    InterviewCard,
    Mismatch,
    ReconcileChoice,
    ReconciliationDecision,
    RepoAudit,
)
from rigor.taxonomy import load_taxonomy

DELETE = "[삭제]"
MAX_CARDS = 8
_NUM = re.compile(r"\s*\d+(?:\.\d+)?\s*(?:%|분|초|시간|배|ms|x|건|명|만|k)?\s*", re.I)

OPTIONS = {
    Mismatch.MISSING_IN_CODE: [
        ReconcileChoice.REVISE_TO_FACT,
        ReconcileChoice.IMPLEMENT_THEN_REAUDIT,
        ReconcileChoice.ADD_EVIDENCE,
        ReconcileChoice.KEEP_AS_IS,
        ReconcileChoice.LATER,
    ],
    Mismatch.METRIC_UNSUPPORTED: [
        ReconcileChoice.REVISE_TO_FACT,
        ReconcileChoice.IMPLEMENT_THEN_REAUDIT,
        ReconcileChoice.KEEP_AS_IS,
        ReconcileChoice.LATER,
    ],
    Mismatch.CONTRIBUTION_OVERSTATED: [
        ReconcileChoice.REVISE_TO_FACT,
        ReconcileChoice.ADD_EVIDENCE,
        ReconcileChoice.KEEP_AS_IS,
        ReconcileChoice.LATER,
    ],
    Mismatch.AI_ASSISTED_UNEXPLAINED: [ReconcileChoice.REVISE_TO_FACT, ReconcileChoice.LATER],
}
_MISMATCH = {
    (ClaimType.TECH, ClaimResult.NOT_FOUND): Mismatch.MISSING_IN_CODE,
    (ClaimType.FEATURE, ClaimResult.NOT_FOUND): Mismatch.MISSING_IN_CODE,
    (ClaimType.METRIC, ClaimResult.NOT_FOUND): Mismatch.METRIC_UNSUPPORTED,
    (ClaimType.ROLE, ClaimResult.CONTRADICTED): Mismatch.CONTRIBUTION_OVERSTATED,
}


@dataclass
class ReconcileItem:
    fact: Fact
    claim: Claim
    check: ClaimCheck | None
    mismatch: Mismatch
    suggestion: str

    @property
    def options(self) -> list[ReconcileChoice]:
        return OPTIONS[self.mismatch]


@dataclass
class ReconcileOutcome:
    evidence_profile: Profile
    decisions: list[ReconciliationDecision]
    differs_from_evidence: list[str] = field(
        default_factory=list
    )  # keep_as_is → 리포트 "근거와 다름"
    to_implement: list[str] = field(default_factory=list)
    ai_notes: list[str] = field(default_factory=list)


def _areas_text(contribution: ContributionAnalysis | None) -> str:
    if not contribution:
        return "담당 영역"
    mine = [a.path_prefix for a in contribution.areas if a.user_changes > 0][:3]
    return ", ".join(mine) or "담당 영역"


def suggest_revision(
    claim: Claim, mismatch: Mismatch, contribution: ContributionAnalysis | None
) -> str:
    if mismatch is Mismatch.METRIC_UNSUPPORTED:
        stripped = _NUM.sub(" ", claim.text).strip()
        return stripped if stripped and stripped != claim.text else DELETE
    if mismatch is Mismatch.CONTRIBUTION_OVERSTATED:
        team = (
            f"{contribution.total_commits}개 커밋 규모의 팀 프로젝트에서 "
            if contribution
            else "팀 프로젝트에서 "
        )
        return f"{team}{_areas_text(contribution)} 작업 담당"
    if mismatch is Mismatch.AI_ASSISTED_UNEXPLAINED:
        return f"{claim.text} (AI 코딩 도구를 활용해 구현)"
    return _without_listed_skills(claim.text) or DELETE  # 코드에 없으면 빼는 것이 사실에 맞다


def _without_listed_skills(text: str) -> str | None:
    """'A·B·C'처럼 나열된 기술만 뺀다. 나열이 아니면 None (문장 전체 삭제 제안)."""
    aliases = {
        a for a, canonical in load_taxonomy().aliases().items() if canonical in skills_in_text(text)
    }
    out = text
    for alias in sorted(aliases, key=len, reverse=True):
        name = re.escape(alias)
        out = re.sub(rf"{name}\s*[·,/]\s*|\s*[·,/]\s*{name}(?![A-Za-z0-9])", "", out, flags=re.I)
    return out if out != text else None


def find_mismatches(profile: Profile, audits: dict[int, RepoAudit]) -> list[ReconcileItem]:
    facts = {f.id: f for f in profile.facts}
    claims = {c.id: c for f in profile.facts for c in f.claims}
    items = []
    for audit in audits.values():
        for chk in audit.claim_checks:
            claim = claims.get(chk.claim_id)
            mismatch = _MISMATCH.get((claim.type, chk.result)) if claim else None
            if mismatch:
                items.append(
                    ReconcileItem(
                        facts[chk.fact_id],
                        claim,
                        chk,
                        mismatch,
                        suggest_revision(claim, mismatch, audit.contribution),
                    )
                )
    return items


ReconcileChooser = Callable[[ReconcileItem], tuple[ReconcileChoice, str | None]]
ExplainChooser = Callable[[Fact], bool]


def default_choice(item: ReconcileItem) -> tuple[ReconcileChoice, str | None]:
    """비대화형 기본값: 사실에 맞게 고친다 (ADR-0017 기본 추천)."""
    return ReconcileChoice.REVISE_TO_FACT, item.suggestion


def reconcile(
    profile: Profile,
    audits: dict[int, RepoAudit],
    choose: ReconcileChooser = default_choice,
    can_explain: ExplainChooser = lambda _f: True,
) -> ReconcileOutcome:
    items = find_mismatches(profile, audits)
    decisions: list[ReconciliationDecision] = []
    drop: set[str] = set()
    revised: dict[str, str] = {}
    outcome_lists: dict[str, list[str]] = {"keep": [], "impl": []}
    for item in items:
        choice, text = choose(item)
        if choice is ReconcileChoice.REVISE_TO_FACT:
            text = text or item.suggestion
        decisions.append(
            ReconciliationDecision(
                claim_id=item.claim.id,
                mismatch=item.mismatch,
                options_offered=item.options,
                choice=choice,
                revised_text=text if choice is ReconcileChoice.REVISE_TO_FACT else None,
            )
        )
        if choice is ReconcileChoice.REVISE_TO_FACT:
            if text == DELETE:
                drop.add(item.claim.id)
            else:
                revised[item.claim.id] = text
        else:
            drop.add(item.claim.id)
            if choice is ReconcileChoice.KEEP_AS_IS:
                outcome_lists["keep"].append(
                    f"{item.claim.text} — {item.check.action if item.check else ''}"
                )
            if choice is ReconcileChoice.IMPLEMENT_THEN_REAUDIT:
                outcome_lists["impl"].append(item.claim.text)

    ai_notes = []
    ai_repos = {
        rid for rid, a in audits.items() if a.contribution and a.contribution.ai_coauthored_commits
    }
    for f in profile.facts:
        ai = f.implementation in (Implementation.AI_ASSISTED, Implementation.MOSTLY_AI) or (
            f.repo_link and f.repo_link.repo_id in ai_repos
        )
        if ai and f.claims and not can_explain(f):
            ai_notes.append(
                f"{f.content}: 설계 결정과 검증 방법을 설명할 수 있게 준비하거나, "
                "'AI 코딩 도구를 활용해 구현'으로 사실대로 표현하세요"
            )

    facts = []
    for f in profile.facts:
        changed = any(c.id in revised or c.id in drop for c in f.claims)
        claims = [
            c.model_copy(update={"text": revised[c.id]}) if c.id in revised else c
            for c in f.claims
            if c.id not in drop
        ]
        status = FactStatus.REVISED if changed and f.status is FactStatus.CONFIRMED else f.status
        facts.append(f.model_copy(update={"claims": claims, "status": status}))
    return ReconcileOutcome(
        profile.model_copy(update={"facts": facts}),
        decisions,
        outcome_lists["keep"],
        outcome_lists["impl"],
        ai_notes,
    )


class CardDraft(DraftModel):
    claim_id: str
    likely_questions: list[str] = Field(min_length=1)
    talking_points: list[str] = Field(default_factory=list)
    caution: str | None = None


class CardsDraft(DraftModel):
    cards: list[CardDraft]


def card_targets(
    profile: Profile, audits: dict[int, RepoAudit]
) -> list[tuple[Fact, Claim, list[Citation], str]]:
    """면접에서 설명이 필요한 claim: 팀 프로젝트 역할, 수치, 저장소로 확인 불가, AI 도구 사용."""
    checks = {c.claim_id: (rid, c) for rid, a in audits.items() for c in a.claim_checks}
    per_fact: list[list[tuple[int, tuple[Fact, Claim, list[Citation], str]]]] = []
    for f in profile.evidence_facts():
        if f.category not in (FactCategory.PROJECT, FactCategory.EXPERIENCE):
            continue
        ranked = []
        for c in f.claims:
            if c.type in (ClaimType.LINK, ClaimType.PERIOD):
                continue
            rid, chk = checks.get(c.id, (None, None))
            audit = audits.get(rid) if rid else None
            reasons = []  # 면접에서 먼저 물을 만한 순서
            if f.attribution is Attribution.TEAM and c.type is ClaimType.ROLE:
                reasons.append("팀 프로젝트 역할")
            if c.type is ClaimType.METRIC:
                reasons.append("수치 성과")
            if f.implementation in (Implementation.AI_ASSISTED, Implementation.MOSTLY_AI) or (
                audit and audit.contribution and audit.contribution.ai_coauthored_commits
            ):
                reasons.append("AI 코딩 도구 사용")
            if chk is None or chk.result is ClaimResult.UNVERIFIABLE:
                reasons.append("저장소로 확인 불가")
            if not reasons:
                continue
            cites = (
                list(chk.evidence) if chk and chk.evidence else [Citation(kind="fact", ref_id=f.id)]
            )
            stats = ""
            if audit and audit.contribution:
                ac = audit.contribution
                stats = f"본인 커밋 {ac.user_commits}/{ac.total_commits}, AI 공동 작성 {ac.ai_coauthored_commits}"
            ranked.append((len(reasons), (f, c, cites, f"{'; '.join(reasons)}. {stats}".strip())))
        if ranked:
            per_fact.append(sorted(ranked, key=lambda x: -x[0]))
    # 앞 항목에 몰리지 않게 항목별로 돌아가며 고른다
    out = []
    while per_fact and len(out) < MAX_CARDS:
        for items in per_fact:
            if items and len(out) < MAX_CARDS:
                out.append(items.pop(0)[1])
        per_fact = [items for items in per_fact if items]
    return out


def interview_cards(
    client: LLMClient, profile: Profile, audits: dict[int, RepoAudit]
) -> tuple[list[InterviewCard], list[str]]:
    targets = card_targets(profile, audits)
    if not targets:
        return [], []
    prompt = load_prompt("interview_card")
    lines = [
        f"[{c.id}] {f.content} — 주장: {c.text} | 맥락: {ctx} | 근거: "
        + "; ".join(x.quote or x.ref_id for x in cites)
        for f, c, cites, ctx in targets
    ]
    result = client.structured(
        "interview_card",
        system=prompt.text,
        messages=[{"role": "user", "content": data_block("claims", "\n".join(lines))}],
        schema=CardsDraft,
    )
    by_claim = {c.id: cites for _, c, cites, _ in targets}
    cards, warnings = [], []
    for d in result.value.cards:
        if d.claim_id not in by_claim:
            warnings.append(f"card for unknown claim {d.claim_id} dropped")
            continue
        cards.append(
            InterviewCard(
                claim_id=d.claim_id,
                likely_questions=d.likely_questions,
                evidence_locations=by_claim[d.claim_id],
                talking_points=d.talking_points,
                caution=d.caution,
            )
        )
    return cards, warnings
