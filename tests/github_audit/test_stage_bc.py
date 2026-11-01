import json

import pytest

from rigor.github_audit.api_source import API, RAW, GitHubSource
from rigor.github_audit.audit import audit_repo, repo_facts
from rigor.github_audit.snapshot import RepoMeta
from rigor.nodes.repo_linking import (
    LinkStatus,
    RepoFacts,
    apply_link_checks,
    check_links,
    find_unlinked,
    propose,
    resolve_unlinked,
)
from rigor.schemas.profile import ClaimType, Fact, FactCategory, RepoLink, UnlinkedReason
from rigor.schemas.repo import Mismatch, UnlinkedProposal
from rigor.synth.cases import base_cases
from rigor.synth.harness import (
    exposure_context,
    profile_after_stage_a,
    snapshots,
    truth_chooser,
)

CASES = base_cases()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """케이스마다 git 저장소를 한 번만 만든다."""
    out = {}
    for case in CASES:
        root = tmp_path_factory.mktemp(case.case_id)
        snaps = snapshots(case, root)
        identity = case.profile.identity
        catalog = [repo_facts(s, identity) for s in snaps.values()]
        profile = profile_after_stage_a(case)
        profile = apply_link_checks(profile, check_links(profile, catalog))
        items = find_unlinked(profile, catalog)
        profile, resolved = resolve_unlinked(profile, items, catalog, truth_chooser(case))
        audits, pending = {}, {}
        for rid, snap in snaps.items():
            facts = [f for f in profile.facts if f.repo_link and f.repo_link.repo_id == rid]
            result = audit_repo(snap, identity, facts, exposure_context(case))
            audits[rid], pending[rid] = result.audit, result.pending
        no_repo = [
            f for f in profile.facts if f.claims and not (f.repo_link and f.repo_link.repo_id)
        ]
        out[case.case_id] = dict(
            case=case,
            snaps=snaps,
            catalog=catalog,
            items=items,
            resolved=resolved,
            profile=profile,
            audits=audits,
            pending=pending,
            no_repo=no_repo,
        )
    return out


@pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
def test_resume_links_are_valid(built, case_id):
    b = built[case_id]
    checks = check_links(profile_after_stage_a(b["case"]), b["catalog"])
    assert {c.status for c in checks} == {LinkStatus.OK}
    assert len(checks) == len(b["case"].linked_in_resume)


def test_forgotten_repo_is_the_top_candidate(built):
    b = built["A_agent_strong"]
    item = next(i for i in b["items"] if i.fact_id == "f_a3")
    assert item.candidates[0].repo_id == 103
    assert any("기술 겹침" in r for r in item.candidates[0].reasons)
    linked = next(f for f in b["profile"].facts if f.id == "f_a3")
    assert (
        linked.repo_link.repo_id == 103
        and linked.repo_link.unlinked_reason is UnlinkedReason.FORGOT
    )


def test_company_experience_is_kept_for_interview_prep(built):
    b = built["A_agent_strong"]
    r = next(i for i in b["resolved"] if i.fact_id == "f_a4")
    assert r.proposal is UnlinkedProposal.KEEP_WITH_INTERVIEW_PREP


@pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
def test_rule_claim_checks_match_truth_and_never_contradict_it(built, case_id):
    b = built[case_id]
    truth = {c.claim_id: c for c in b["case"].truth.claims}
    checks = [c for a in b["audits"].values() for c in a.claim_checks]
    from rigor.github_audit.claims import check_claims_by_rules

    for f in b["no_repo"]:
        checks += check_claims_by_rules(f, None, None, None).checks
    for c in checks:
        assert c.result is truth[c.claim_id].result, (c.claim_id, c.result, c.action)
    # 규칙이 맡는 유형(기술·수치 claim, 기여 과장)은 반드시 판정되어야 한다
    claims = {c.id: c for f in b["case"].profile.facts for c in f.claims}
    decided = {c.claim_id for c in checks}
    must = {
        t.claim_id
        for t in truth.values()
        if claims[t.claim_id].type in (ClaimType.TECH, ClaimType.METRIC)
        or t.mismatch is Mismatch.CONTRIBUTION_OVERSTATED
    }
    assert must <= decided, must - decided


def test_feature_claim_without_a_tech_name_is_left_pending(built):
    # "RAG 기반 사내 문서 검색 챗봇 구현"은 규칙으로 판단할 수 없다 → 근거 검색 + LLM (M1b)
    pending = {c.id for p in built["B_data_redirect"]["pending"].values() for c in p}
    assert "c_b2_rag" in pending


@pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
def test_contribution_matches_truth(built, case_id):
    b = built[case_id]
    for rt in b["case"].truth.repos:
        contrib = b["audits"][rt.repo_id].contribution
        assert contrib.user_share == pytest.approx(rt.user_share)
        assert contrib.ai_coauthored_commits == rt.ai_coauthored_commits


def test_alias_emails_change_the_share(built):
    from rigor.github_audit.contribution import analyze_contribution

    b = built["D_contribution_overstated"]
    identity = b["case"].profile.identity
    only_account = identity.model_copy(update={"emails": identity.emails[:1]})
    snap = b["snaps"][401]
    assert analyze_contribution(snap, only_account).user_share == pytest.approx(2 / 30)
    assert analyze_contribution(snap, identity).user_share == pytest.approx(4 / 30)


@pytest.mark.parametrize("case_id", [c.case_id for c in CASES])
def test_exposure_findings_match_planted_ones(built, case_id):
    b = built[case_id]
    found = {(rid, e.kind, e.path) for rid, a in b["audits"].items() for e in a.exposure_findings}
    want = {(e.repo_id, e.kind, e.path) for e in b["case"].truth.exposures}
    assert found == want
    for a in b["audits"].values():
        for e in a.exposure_findings:
            assert "AKIA" not in e.detail  # 값 자체는 결과에 담지 않는다


def test_signals_reflect_designed_quality(built):
    a = built["A_agent_strong"]["audits"]
    s101, s102 = a[101].signals, a[102].signals
    assert s101.tests.exists and s101.ci.exists and s101.license == "MIT"
    assert set(s101.readme.sections) == {"problem", "design", "setup", "results"}
    assert {"langgraph", "faiss"} <= set(s101.dependencies)
    assert not s102.ci.exists and set(s102.readme.sections) == {"problem", "setup"}
    assert not built["B_data_redirect"]["audits"][202].signals.tests.exists


def test_injection_in_readme_is_counted(tmp_path):
    from rigor.synth.variants import inject

    case = inject(CASES[0], "readme")
    snaps = snapshots(case, tmp_path)
    result = audit_repo(snaps[101], case.profile.identity, [], exposure_context(case))
    assert result.audit.signals.injection_suspects == 1


def test_link_to_someone_elses_repo_is_not_analyzed():
    meta = RepoMeta(repo_id=1, full_name="me/mine")
    fork = RepoMeta(repo_id=2, full_name="me/forked", is_fork=True)
    catalog = [RepoFacts(meta=meta, user_commits=3), RepoFacts(meta=fork, user_commits=0)]
    case = CASES[0]
    profile = case.profile.model_copy(
        update={
            "facts": [
                case.profile.facts[0].model_copy(
                    update={"repo_link": RepoLink(url="https://github.com/other/theirs")}
                ),
                case.profile.facts[1].model_copy(
                    update={"repo_link": RepoLink(url="https://github.com/me/forked")}
                ),
            ]
        }
    )
    statuses = [c.status for c in check_links(profile, catalog)]
    assert statuses == [LinkStatus.NOT_IN_ACCOUNT, LinkStatus.FORK_WITHOUT_CHANGES]


@pytest.mark.parametrize(
    ("category", "reason", "repo", "expected"),
    [
        (
            FactCategory.PROJECT,
            UnlinkedReason.NO_REPO,
            None,
            UnlinkedProposal.ADD_EVIDENCE_OR_REDUCE,
        ),
        (
            FactCategory.EXPERIENCE,
            UnlinkedReason.NO_REPO,
            None,
            UnlinkedProposal.KEEP_WITH_INTERVIEW_PREP,
        ),
        (FactCategory.PROJECT, UnlinkedReason.LOW_QUALITY, 5, UnlinkedProposal.PRIVATE_AUDIT),
        (FactCategory.PROJECT, UnlinkedReason.FORGOT, 5, UnlinkedProposal.LINK),
        (
            FactCategory.EXPERIENCE,
            UnlinkedReason.PRIVATE_OR_COMPANY,
            None,
            UnlinkedProposal.KEEP_WITH_INTERVIEW_PREP,
        ),
    ],
)
def test_unlinked_proposal_rules(category, reason, repo, expected):
    fact = Fact(id="f_1", category=category, content="x")
    assert propose(fact, reason, repo) is expected


def test_api_source_builds_snapshot_without_network():
    full = "o/r"
    responses = {
        f"{API}/repos/{full}": {
            "id": 7,
            "full_name": full,
            "description": "d",
            "topics": ["rag"],
            "fork": False,
            "default_branch": "main",
            "size": 12,
            "homepage": None,
        },
        f"{API}/repos/{full}/commits/main": {"sha": "abc"},
        f"{API}/repos/{full}/git/trees/abc?recursive=1": {
            "tree": [
                {"path": "README.md", "type": "blob", "size": 20},
                {"path": "pyproject.toml", "type": "blob", "size": 60},
                {"path": "src/app.py", "type": "blob", "size": 30},
                {"path": "assets/big.bin", "type": "blob", "size": 9_000_000},
            ],
            "truncated": False,
        },
        f"{RAW}/{full}/abc/README.md": b"# R\n## Results\n",
        f"{RAW}/{full}/abc/pyproject.toml": b'[project]\nname="r"\ndependencies=["langgraph"]\n',
        f"{RAW}/{full}/abc/src/app.py": b"import faiss\n",
        f"{API}/repos/{full}/commits?sha=abc&per_page=100&page=1": [
            {
                "sha": "abc",
                "commit": {
                    "author": {"name": "me", "email": "Me@X.com", "date": "2025-01-02T03:04:05Z"},
                    "message": "init",
                },
            }
        ],
    }

    def fetch(url):
        body = responses[url]
        return body if isinstance(body, bytes) else json.dumps(body).encode()

    snap = GitHubSource(fetch).snapshot(full)
    assert snap.scope == "api_partial" and snap.head_sha == "abc"
    assert set(snap.files) == {"README.md", "pyproject.toml", "src/app.py"}  # 큰 바이너리는 안 받음
    assert snap.commits[0].author_email == "me@x.com"
    from rigor.github_audit.signals import compute_signals

    assert {"langgraph", "faiss"} <= set(compute_signals(snap).dependencies)


def test_selection_includes_contributed_team_repo_and_skips_others():
    from datetime import UTC, datetime

    from rigor.github_audit.selection import identity_from_account, select_repos
    from rigor.github_audit.snapshot import CommitInfo, RepoSnapshot

    owned = [RepoMeta(1, "me/blog", "개인 기술 블로그"), RepoMeta(2, "me/dotfiles", "설정")]
    base = CASES[0].profile
    facts = [
        base.facts[0].model_copy(
            update={"repo_link": RepoLink(url="https://github.com/team/project")}
        ),
        base.facts[1].model_copy(
            update={"repo_link": RepoLink(url="https://github.com/stranger/repo")}
        ),
        base.facts[2],  # 개인 기술 블로그 (링크 없음)
    ]
    profile = base.model_copy(update={"facts": facts})
    sel = select_repos(profile, owned, "me", lambda name, login: name == "team/project")
    assert sel.full_names[0] == "team/project"
    assert "me/blog" in sel.full_names and "stranger/repo" in sel.skipped_not_mine

    when = datetime(2025, 1, 1, tzinfo=UTC)
    snap = RepoSnapshot(
        RepoMeta(3, "team/project"),
        "sha",
        [],
        {},
        [
            CommitInfo("a", "Me", "me@work.com", when, "x", author_login="me"),
            CommitInfo("b", "Other", "o@x.com", when, "y", author_login="other"),
        ],
    )
    assert identity_from_account("me", {3: snap}).emails == ["me@work.com"]
