from datetime import UTC, datetime

from rigor.github_audit.audit import audit_repo
from rigor.github_audit.exposure import ExposureContext
from rigor.github_audit.snapshot import CommitInfo, RepoMeta, RepoSnapshot
from rigor.schemas.profile import Claim, ClaimType, CommitIdentity, Fact, FactCategory, FactStatus

COMMIT = CommitInfo("a", "n", "a@example.com", datetime(2025, 1, 1, tzinfo=UTC), "m")


def snap(**extra):
    files = {"README.md": "accuracy 42% latency 30ms\n", **extra}
    return RepoSnapshot(RepoMeta(1, "o/r"), "h", sorted(files), files, [COMMIT])


def fact(i, text):
    claim = Claim(id=f"c_{i}", fact_id=f"f_{i}", type=ClaimType.METRIC, text=text)
    return Fact(
        id=f"f_{i}",
        category=FactCategory.PROJECT,
        content="x",
        status=FactStatus.CONFIRMED,
        claims=[claim],
    )


def test_two_facts_on_one_repo_get_distinct_evidence_ids():
    identity = CommitIdentity(github_login="o", emails=["a@example.com"], confirmed_by_user=True)
    facts = [fact(1, "accuracy 42%"), fact(2, "latency 30ms")]
    audit = audit_repo(snap(), identity, facts, ExposureContext()).audit
    by_id = {e.id: e for e in audit.evidence}
    assert len(by_id) == len(audit.evidence) == 2
    # 각 판정의 인용이 자기 근거를 가리켜야 한다
    for check, number in zip(audit.claim_checks, ("42", "30"), strict=True):
        assert number in by_id[check.evidence[0].ref_id].quote
