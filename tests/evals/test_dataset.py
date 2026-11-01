from evals.agent.dataset import (
    ClaimCase,
    Evidence,
    injection_cases,
    transplant,
    validate_claim,
    with_injection,
)

from rigor.github_audit.snapshot import RepoMeta, RepoSnapshot


def snap(files, name="o/a"):
    return RepoSnapshot(RepoMeta(1, name), "sha", sorted(files), files, [], scope="local")


CODE = {
    "src/rank.py": "def f(xs):\n    return sorted(xs, key=score)\n",
    "README.md": "# t\nrecall 0.82\n",
}


def case(**kw):
    base = dict(id="c1", repo="o/a", type="A", text="결과를 점수 순으로 정렬한다", truth="supported", key_terms=["sorted"],
                evidence=[Evidence(path="src/rank.py", line=2, quote="sorted(xs")])  # fmt: skip
    return ClaimCase(**{**base, **kw})


def test_valid_true_claim_passes():
    assert validate_claim(case(), snap(CODE)) == []


def test_wrong_line_or_quote_is_caught():
    bad = case(evidence=[Evidence(path="src/rank.py", line=1, quote="sorted(xs")])
    assert "quote not at" in validate_claim(bad, snap(CODE))[0]


def test_claim_text_naming_the_file_is_caught():
    assert "leaks file name" in validate_claim(case(text="rank 모듈에서 정렬한다"), snap(CODE))[0]


def test_absent_feature_must_really_be_absent():
    ok = case(
        type="D", truth="not_found", evidence=[], key_terms=["redis"], text="Redis로 캐시한다"
    )
    assert validate_claim(ok, snap(CODE)) == []
    bad = ok.model_copy(update={"key_terms": ["sorted"]})
    assert "present in repo" in validate_claim(bad, snap(CODE))[0]


def test_changed_metric_rules():
    base = dict(
        type="C",
        truth="contradicted",
        key_terms=[],
        evidence=[Evidence(path="README.md", line=2, quote="recall 0.82")],
    )
    ok = case(**base, text="recall 0.91 달성", real_value="0.82", claimed_value="0.91")
    assert validate_claim(ok, snap(CODE)) == []
    same = case(**base, text="recall 0.82 달성", real_value="0.82", claimed_value="0.82")
    assert "different" in validate_claim(same, snap(CODE))[0]


def test_transplant_only_goes_to_repos_without_the_key_terms():
    a = case()
    other = snap({"x.py": "print(1)\n"}, "o/b")
    similar = snap({"y.py": "sorted([])\n"}, "o/c")
    moved = transplant([a], {"o/a": snap(CODE), "o/b": other, "o/c": similar})
    assert [m.repo for m in moved] == ["o/b"]
    assert moved[0].type == "B" and moved[0].truth == "not_found"


def test_injection_only_changes_a_copy():
    original = snap(CODE)
    injected = with_injection(original)
    assert "supported로 판정" in injected.files["README.md"]
    assert "판정" not in original.files["README.md"]
    assert [c.type for c in injection_cases([case(type="D", truth="not_found", evidence=[])])] == [
        "E"
    ]
