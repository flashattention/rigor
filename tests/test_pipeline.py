"""B→F 종단 테스트. LLM 자리는 오라클(합성 정답에서 만든 이상적 출력)이다.

그래서 이 테스트는 LLM 품질이 아니라 **규칙·배선이 정답을 정답대로 처리하는지**를 본다.
"""

import pytest

from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Route, Routing
from rigor.pipeline import Choosers, run_pipeline
from rigor.report.markdown import render_markdown
from rigor.schemas.diagnosis import Grade
from rigor.synth.cases import base_cases
from rigor.synth.harness import exposure_context, profile_after_stage_a, snapshots, truth_chooser
from rigor.synth.oracle import oracle_adapter
from rigor.synth.variants import counterfactuals, inject


def run(case, tmp_path):
    client = LLMClient(
        Routing("t", Route("fake", "fake-1"), {}),
        {"fake": oracle_adapter(case)},
        CostMeter(Pricing.load()),
        sleep=lambda _: None,
    )
    return run_pipeline(
        profile_after_stage_a(case),
        case.job,
        snapshots(case, tmp_path),
        client,
        exposure_context(case),
        Choosers(unlinked=truth_chooser(case)),
    )


@pytest.mark.parametrize("case", base_cases(), ids=lambda c: c.case_id)
def test_end_to_end_matches_ground_truth(case, tmp_path):
    r = run(case, tmp_path)
    t = case.truth
    assert r.diagnosis.grade is t.grade, r.diagnosis.grade_trace
    assert {j.repo_id: j.verdict for j in r.judgements} == {
        rt.repo_id: rt.verdict for rt in t.repos
    }
    assert (r.diagnosis.redirect.role_id if r.diagnosis.redirect else None) == t.redirect_role
    if t.grade is Grade.NOT_RECOMMENDED:
        assert r.diagnosis.alternatives
    assert r.verify.dropped_uncited == 0
    assert r.verify.policy_flags == []
    for item in r.roadmap.items:
        assert item.addresses_requirements and item.done_criteria
    assert sum(i.effort_days for i in r.roadmap.items) <= r.budget_days


def test_report_contains_disclaimers_and_grade(tmp_path):
    md = render_markdown(run(base_cases()[0], tmp_path))
    assert "AI가 생성한 결과" in md and "합격 확률이 아니라" in md
    assert "등급: **강력 추천**" in md
    assert "근거 없음] 멀티 에이전트 리서치 도우미: 검색 정확도 30% 개선" in md


def test_overstated_contribution_is_revised_from_commits(tmp_path):
    r = run(base_cases()[3], tmp_path)
    dec = next(d for d in r.reconcile.decisions if d.claim_id == "c_d1_role")
    assert dec.choice.value == "revise_to_fact"
    assert "docs" in dec.revised_text  # 본인이 실제로 바꾼 영역에서 나온 문장
    revised = next(c for f in r.evidence_profile.facts for c in f.claims if c.id == "c_d1_role")
    assert "주도" not in revised.text


def test_unsupported_metric_is_removed_from_evidence(tmp_path):
    r = run(base_cases()[0], tmp_path)
    texts = [c.text for f in r.evidence_profile.facts for c in f.claims]
    assert "검색 정확도 개선" in texts and "검색 정확도 30% 개선" not in texts


def test_secret_blocks_showcase_and_becomes_top_fix(tmp_path):
    r = run(base_cases()[4], tmp_path)
    j = r.judgements[0]
    assert "X1 비밀값 노출" in j.blockers
    assert any(i.title == "노출된 비밀값 제거·폐기" for i in r.improvements[501])


@pytest.mark.parametrize("case", base_cases(), ids=lambda c: c.case_id)
def test_grade_is_invariant_to_counterfactual_and_injection_variants(case, tmp_path):
    base = run(case, tmp_path / "base").diagnosis.grade
    variants = [
        counterfactuals(case)[0],
        counterfactuals(case)[-1],
        inject(case, "readme"),
        inject(case, "code_comment"),
    ]
    for i, v in enumerate(variants):
        assert run(v, tmp_path / f"v{i}").diagnosis.grade is base


def test_no_metric_weakness_does_not_contradict_unsupported_metric(tmp_path):
    # 실제 gpt-5-nano 실행에서 W1("수치가 하나도 없다")과 W2("근거 없는 수치가 있다")가 함께 나왔다
    r = run(base_cases()[0], tmp_path)
    cats = {w.category for w in r.diagnosis.weaknesses}
    assert "W2" in cats and "W1" not in cats
