from evals.agent.dataset import ClaimCase
from evals.agent.runner import (
    CaseResult,
    SearchTerms,
    check_cases,
    evaluate_case,
    run_rules,
    score,
)

from rigor.agent.verdict import VerdictSubmission
from rigor.github_audit.snapshot import RepoMeta, RepoSnapshot
from rigor.llm.adapters.fake import FakeAdapter
from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Route, Routing
from rigor.llm.types import ToolCall, ToolTurn, Usage

FILES = {"src/rank.py": "def f(xs):\n    return sorted(xs, key=score)\n", "README.md": "# t\n"}
SNAP = RepoSnapshot(RepoMeta(1, "o/a"), "s", sorted(FILES), FILES, [], scope="local")
TRUE = ClaimCase(
    id="c1", repo="o/a", type="A", text="결과를 점수 순으로 정렬한다", truth="supported"
)


def client(fake):
    return LLMClient(
        Routing("t", Route("fake", "fake-1"), {}),
        {"fake": fake},
        CostMeter(Pricing.load()),
        sleep=lambda _: None,
    )


def r(**kw):
    return CaseResult(
        **{
            "case_id": "x",
            "type": "A",
            "truth": "supported",
            "method": "m",
            "result": "supported",
            **kw,
        }
    )


def test_score_separates_the_failures_that_matter():
    results = [
        r(),  # 참 → 확인됨
        r(result="not_found"),  # 참을 거짓으로 지적: 가장 해로움
        r(result="unverifiable"),
        r(type="D", truth="not_found", result="supported"),  # 거짓을 통과시킴
        r(type="E", truth="not_found", result="not_found"),
    ]
    s = score(results)
    assert s["supported_precision"] == 0.5
    assert s["false_accusation_rate"] == round(1 / 3, 3)
    assert s["false_claim_recall"] == 0.5
    assert s["abstain_rate"] == 0.2
    assert s["injection_defense"] == 1.0


def test_score_reports_none_not_zero_when_there_is_no_denominator():
    assert (
        score([r(type="D", truth="not_found", result="not_found")])["supported_precision"] is None
    )


def test_rules_abstain_when_they_cannot_decide():
    assert run_rules(TRUE, SNAP).result.value == "unverifiable"


def test_keyword_method_reads_hits_and_checks_citations_in_code():
    good = VerdictSubmission(
        result="supported",
        reason="r",
        citations=[{"path": "src/rank.py", "line": 2, "quote": "sorted(xs"}],
    )
    fake = FakeAdapter().queue("claim_verify", [SearchTerms(terms=["sorted"]), good])
    assert evaluate_case(client(fake), "keyword", TRUE, SNAP).result == "supported"
    forged = VerdictSubmission(
        result="supported",
        reason="r",
        citations=[{"path": "src/rank.py", "line": 2, "quote": "import magic"}],
    )
    fake = FakeAdapter().queue("claim_verify", [SearchTerms(terms=["sorted"]), forged])
    out = evaluate_case(client(fake), "keyword", TRUE, SNAP)
    assert out.result == "unverifiable" and out.downgraded


def test_verified_citations_are_kept_for_failure_analysis():
    good = VerdictSubmission(
        result="supported",
        reason="정렬 코드",
        citations=[{"path": "src/rank.py", "line": 2, "quote": "sorted(xs"}],
    )
    fake = FakeAdapter().queue("claim_verify", [SearchTerms(terms=["sorted"]), good])
    out = evaluate_case(client(fake), "keyword", TRUE, SNAP)
    assert out.reason == "정렬 코드"
    assert out.citations == [{"path": "src/rank.py", "line": 2, "quote": "sorted(xs"}]


def test_badly_written_claims_are_found_before_spending_money():
    wrong = ClaimCase(id="c2", repo="o/a", type="D", text="없는 기능", truth="supported")
    assert any("c2" in p for p in check_cases([wrong], {"o/a": SNAP}))


def test_agent_method_runs_the_loop_and_one_failure_does_not_stop_the_run():
    use = Usage(input_tokens=10, output_tokens=1)
    read = ToolTurn("", (ToolCall("a", "read_file", {"path": "src/rank.py"}),), use)
    done = ToolTurn(
        "",
        (
            ToolCall(
                "b",
                "submit_verdict",
                {
                    "result": "supported",
                    "reason": "r",
                    "citations": [{"path": "src/rank.py", "line": 2, "quote": "sorted(xs"}],
                },
            ),
        ),
        use,
    )
    fake = FakeAdapter().queue_turns("claim_verify", [read, done, RuntimeError("boom")])
    c = client(fake)
    ok = evaluate_case(c, "agent", TRUE, SNAP)
    assert ok.result == "supported" and ok.steps == 2 and ok.stop_reason == "submitted"
    bad = evaluate_case(c, "agent", TRUE, SNAP)
    assert bad.result == "unverifiable" and bad.error == "RuntimeError"
