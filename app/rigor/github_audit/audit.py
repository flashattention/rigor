"""C단계 묶음: 스냅샷 → 신호 · 노출 · 기여 · claim 규칙 검증 → RepoAudit (저장소마다 독립)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from rigor.github_audit.claims import check_claims_by_rules
from rigor.github_audit.contribution import analyze_contribution
from rigor.github_audit.exposure import ExposureContext, scan_exposure
from rigor.github_audit.signals import compute_signals
from rigor.github_audit.snapshot import RepoSnapshot
from rigor.nodes.repo_linking import RepoFacts
from rigor.schemas.profile import Claim, CommitIdentity, Fact
from rigor.schemas.repo import RepoAudit

ANALYZER_VERSION = "c1-rules-0.1"


@dataclass
class RepoAuditResult:
    audit: RepoAudit
    pending: list[Claim]  # 규칙으로 판단 못 한 claim → 근거 검색 + LLM (M1b)


def repo_facts(snap: RepoSnapshot, identity: CommitIdentity) -> RepoFacts:
    """B2 후보 추천용 요약."""
    signals = compute_signals(snap, identity)
    contribution = analyze_contribution(snap, identity)
    period = None
    if contribution.active_period:
        period = tuple(date.fromisoformat(d) for d in contribution.active_period)
    return RepoFacts(
        meta=snap.meta,
        dependencies=tuple(signals.dependencies),
        user_period=period,  # type: ignore[arg-type]
        user_commits=contribution.user_commits,
    )


def audit_repo(
    snap: RepoSnapshot,
    identity: CommitIdentity,
    facts: list[Fact],
    exposure: ExposureContext,
) -> RepoAuditResult:
    signals = compute_signals(snap, identity)
    contribution = analyze_contribution(snap, identity)
    checks, evidence, pending = [], [], []
    for fact in facts:
        result = check_claims_by_rules(fact, snap, signals, contribution)
        checks += result.checks
        evidence += result.evidence
        pending += result.pending
    # 같은 저장소에 여러 fact가 연결될 수 있으므로 근거 id를 저장소 단위로 다시 매긴다
    renamed, mapping = [], {}
    for i, ev in enumerate(evidence, start=1):
        new_id = f"e_{snap.meta.repo_id}_{i}"
        mapping[ev.id] = new_id
        renamed.append(ev.model_copy(update={"id": new_id}))
    if len(mapping) != len(evidence):
        raise AssertionError("evidence id collision")
    checks = [
        c.model_copy(
            update={
                "evidence": [
                    e.model_copy(update={"ref_id": mapping.get(e.ref_id, e.ref_id)})
                    for e in c.evidence
                ]
            }
        )
        for c in checks
    ]
    audit = RepoAudit(
        repo_id=snap.meta.repo_id,
        full_name=snap.meta.full_name,
        visibility=snap.meta.visibility,
        head_sha=snap.head_sha,
        is_fork=snap.meta.is_fork,
        signals=signals,
        contribution=contribution,
        evidence=renamed,
        claim_checks=checks,
        exposure_findings=scan_exposure(snap, exposure),
        scan_scope=snap.scope,
    )
    return RepoAuditResult(audit, pending)
