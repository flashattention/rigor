import pytest

from rigor.ingest.privacy import mask_pii
from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Route, Routing
from rigor.nodes.identity import collect_authors, confirm_aliases, propose_aliases
from rigor.nodes.jd_parser import JobDraft, RequirementDraft, build_job, parse_jd
from rigor.nodes.profile_builder import (
    ClaimDraft,
    FactDraft,
    ProfileDraft,
    build_profile,
    confirm_facts,
    extract_profile,
)
from rigor.schemas.job import LegalCheck, RequirementKind
from rigor.schemas.profile import ClaimType, FactCategory, FactStatus
from rigor.synth.cases import INJECTION, base_cases, case_contribution_overstated
from rigor.synth.gitrepo import materialize_all
from rigor.synth.oracle import oracle_adapter
from rigor.synth.render import render_jd, render_resume
from rigor.synth.variants import inject


def client_for(adapter):
    routing = Routing("test", Route("fake", "fake-1"), {})
    return LLMClient(routing, {"fake": adapter}, CostMeter(Pricing.load()), sleep=lambda _: None)


@pytest.mark.parametrize("case", base_cases(), ids=lambda c: c.case_id)
def test_oracle_extraction_reconstructs_the_case_profile(case):
    mask = mask_pii(render_resume(case))
    result = extract_profile(client_for(oracle_adapter(case, mask)), mask, "p")
    got, want = result.profile.facts, case.profile.facts
    assert [f.content for f in got] == [f.content for f in want]
    assert [len(f.claims) for f in got] == [len(f.claims) for f in want]
    assert all(f.status is FactStatus.UNCONFIRMED for f in got)
    for g, w in zip(got, want, strict=True):
        linked = case.linked_in_resume.get(w.id)
        assert (g.repo_link.url if g.repo_link else None) == (
            case.repo(linked).url if linked else None
        )
    assert result.prompt_id.startswith("fact_extract@v1+")


def test_llm_never_sees_name_school_or_contact():
    case = base_cases()[0]
    mask = mask_pii(render_resume(case))
    adapter = oracle_adapter(case, mask)
    extract_profile(client_for(adapter), mask, "p")
    sent = adapter.requests[0].messages[0]["content"]
    for value in (case.header.name, case.header.school, case.header.phone, case.header.email):
        assert value not in sent
    assert "<resume>" in sent  # 데이터 블록으로 감싸서 보낸다


def test_build_profile_drops_injected_claims_and_unknown_tokens():
    mask = mask_pii("홍길동\n- 프로젝트\n  - 저장소: https://github.com/o/r\n")
    draft = ProfileDraft(
        facts=[
            FactDraft(
                category=FactCategory.PROJECT,
                content="프로젝트",
                start="2025.03",
                end="2025-01",
                repo_refs=["[REPO_9]", "[REPO_1]"],
                skills=["LangGraph", "faiss-cpu"],
                claims=[
                    ClaimDraft(type=ClaimType.TECH, text="LangGraph"),
                    ClaimDraft(type=ClaimType.ROLE, text=INJECTION),
                ],
            )
        ]
    )
    profile, warnings, uncertain = build_profile(draft, mask, "p")
    fact = profile.facts[0]
    assert fact.repo_link.url == "https://github.com/o/r"
    assert [c.text for c in fact.claims] == ["LangGraph"]
    assert fact.skills == ["faiss", "langgraph"]
    assert fact.period.start is None and uncertain["f_1"] == ["period"]
    assert any("REPO_9" in w for w in warnings) and any("injection" in w for w in warnings)


def test_confirm_facts_applies_user_decisions():
    case = base_cases()[0]
    mask = mask_pii(render_resume(case))
    result = extract_profile(client_for(oracle_adapter(case, mask)), mask, "p")
    profile = confirm_facts(
        result, lambda f, _u: FactStatus.REJECTED if f.id == "f_3" else FactStatus.CONFIRMED
    )
    assert [f.id for f in profile.evidence_facts()] == ["f_1", "f_2", "f_4", "f_5"]


@pytest.mark.parametrize("case", base_cases(), ids=lambda c: c.case_id)
def test_oracle_jd_parse_reconstructs_requirements(case):
    text = render_jd(case)
    result = parse_jd(client_for(oracle_adapter(case, mask_pii(""))), text, "j")
    assert [(r.text, r.kind) for r in result.job.requirements] == [
        (r.text, r.kind) for r in case.job.requirements
    ]
    assert result.warnings == []
    assert result.job.blind == case.job.blind


def draft_with(*reqs: RequirementDraft) -> JobDraft:
    return JobDraft(company="c", title="t", requirements=list(reqs))


def test_jd_rules_downgrade_knockout_flag_protected_and_warn_on_paraphrase():
    text = "자격 요건\n- 관련 경력 3년 이상 (필수)\n- 만 30세 이하 남성\n- Python 개발 경험"
    job, warnings = build_job(
        draft_with(
            RequirementDraft(
                text="관련 경력 3년 이상 (필수)", kind="knockout", knockout_type="degree"
            ),
            RequirementDraft(text="만 30세 이하 남성", kind="required"),
            RequirementDraft(text="파이썬 개발 경험이 있는 분", kind="required"),
        ),
        text,
        "j",
    )
    r1, r2, _ = job.requirements
    assert r1.kind is RequirementKind.REQUIRED and r1.knockout_type is None
    assert r2.legal_check is LegalCheck.FLAG
    assert any("r_3" in w and "verbatim" in w for w in warnings)


def test_jd_injection_variant_is_flagged_and_not_a_requirement():
    case = inject(base_cases()[0], "jd")
    text = render_jd(case)
    draft = draft_with(
        RequirementDraft(text="Python 개발 경험", kind="required"),
        RequirementDraft(text=INJECTION, kind="required"),
    )
    job, warnings = build_job(draft, text, "j")
    assert [r.text for r in job.requirements] == ["Python 개발 경험"]
    assert any("injection" in w for w in warnings)


def test_alias_emails_are_offered_and_only_confirmed_ones_added(tmp_path):
    case = case_contribution_overstated()
    paths = materialize_all(case.repos, tmp_path)
    identity = case.profile.identity.model_copy(update={"emails": case.profile.identity.emails[:1]})
    candidates = propose_aliases(identity, collect_authors(paths.values()))
    offered = {c.email for c in candidates}
    assert "dy.laptop@example.org" in offered and identity.emails[0] not in offered
    updated = confirm_aliases(identity, candidates, lambda _c: ["dy.laptop@example.org"])
    assert updated.emails == [identity.emails[0], "dy.laptop@example.org"]
    with pytest.raises(ValueError):
        confirm_aliases(identity, candidates, lambda _c: ["stranger@example.com"])


def test_repo_token_without_brackets_is_accepted():
    # 실제 gpt-5-nano 실행에서 관찰된 출력 형태 ("REPO_1")
    mask = mask_pii("- p\n  - https://github.com/o/r\n")
    draft = ProfileDraft(
        facts=[FactDraft(category=FactCategory.PROJECT, content="p", repo_refs=["REPO_1", "none"])]
    )
    profile, warnings, _ = build_profile(draft, mask, "p")
    assert profile.facts[0].repo_link.url == "https://github.com/o/r"
    assert warnings == []


def test_competency_codes_with_labels_are_normalized():
    # 실제 gpt-5-nano 실행에서 관찰된 출력 형태 ("C08 백엔드·API")
    text = "자격 요건\n- Python 개발 경험"
    job, warnings = build_job(
        draft_with(
            RequirementDraft(
                text="Python 개발 경험",
                kind="required",
                competencies=["C08 백엔드·API", "C99", "x"],
            )
        ),
        text,
        "j",
    )
    assert job.requirements[0].competencies == ["C08"]
    assert any("C99" in w for w in warnings)


def test_claim_with_unknown_type_is_dropped_without_failing_the_run():
    # 실제 이력서 실행에서 gpt-5-nano가 학력 항목에 type="degree"를 지어냈다
    mask = mask_pii("- p\n")
    draft = ProfileDraft.model_validate(
        {
            "facts": [
                {
                    "category": "education",
                    "content": "학사",
                    "claims": [
                        {"type": "degree", "text": "학사"},
                        {"type": "tech", "text": "Python"},
                    ],
                }
            ]
        }
    )
    profile, warnings, _ = build_profile(draft, mask, "p")
    assert [c.text for c in profile.facts[0].claims] == ["Python"]
    assert any("degree" in w for w in warnings)


def test_invalid_fact_or_metric_is_isolated_and_reported():
    # 실제 이력서 실행에서 관찰: category="awards", metric value=null, 없는 category
    draft = ProfileDraft.model_validate(
        {
            "facts": [
                {
                    "category": "awards",
                    "content": "수상",
                    "metrics": [{"name": "x", "value": None, "unit": "%"}],
                },
                {"category": "hobby", "content": "취미"},
            ]
        }
    )
    profile, warnings, _ = build_profile(draft, mask_pii("- p\n"), "p")
    assert [f.category for f in profile.facts] == [FactCategory.AWARD]
    assert profile.facts[0].metrics == []
    assert any("취미" in w for w in warnings)
