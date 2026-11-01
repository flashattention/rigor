import pytest
from pydantic import ValidationError

from rigor.schemas.common import Citation
from rigor.schemas.diagnosis import (
    Alternative,
    Diagnosis,
    Grade,
    GradeTrace,
    RequirementVerdict,
    RoadmapItem,
    RoadmapKind,
    VerdictStatus,
)
from rigor.schemas.job import JobPosting, Requirement, RequirementKind
from rigor.schemas.profile import Claim, ClaimType, Fact, FactCategory, FactStatus, Profile
from rigor.schemas.repo import (
    CheckMethod,
    ClaimCheck,
    ClaimResult,
    DoneCriterion,
    Mismatch,
    ReconcileChoice,
    ReconciliationDecision,
)

CITE = Citation(kind="requirement", ref_id="r_1")


def test_protected_attributes_have_no_fields():
    # ADR-0014: 보호 속성은 스키마에 필드 자체가 없어서 들어올 수 없다.
    with pytest.raises(ValidationError):
        Fact(id="f_1", category=FactCategory.PROJECT, content="x", birth_date="2000-01-01")
    for forbidden in ("gender", "age", "photo", "military", "disability", "address"):
        assert forbidden not in Fact.model_fields
        assert forbidden not in Profile.model_fields


def test_only_confirmed_or_revised_facts_are_evidence():
    facts = [
        Fact(id=f"f_{s.value}", category=FactCategory.PROJECT, content="x", status=s)
        for s in FactStatus
    ]
    profile = Profile(id="p", facts=facts)
    assert {f.status for f in profile.evidence_facts()} == {
        FactStatus.CONFIRMED,
        FactStatus.REVISED,
    }


def test_claim_must_belong_to_its_fact():
    claim = Claim(id="c_1", fact_id="f_other", type=ClaimType.TECH, text="LangGraph")
    with pytest.raises(ValidationError):
        Fact(id="f_1", category=FactCategory.PROJECT, content="x", claims=[claim])


def test_experience_years_is_not_a_knockout_type():
    # ADR-0008 [B3]
    with pytest.raises(ValidationError):
        Requirement(id="r_1", text="3년 이상", kind="knockout", knockout_type="experience_years")


def test_knockout_requires_type_and_default_depths():
    with pytest.raises(ValidationError):
        Requirement(id="r_1", text="학사", kind=RequirementKind.KNOCKOUT)
    req = Requirement(id="r_2", text="Python", kind=RequirementKind.REQUIRED)
    pref = Requirement(id="r_3", text="K8s", kind=RequirementKind.PREFERRED)
    assert (req.effective_min_depth, pref.effective_min_depth) == (2, 1)


def test_job_posting_rejects_duplicate_requirement_ids():
    r = Requirement(id="r_1", text="Python", kind="required")
    with pytest.raises(ValidationError):
        JobPosting(id="j", company="c", title="t", requirements=[r, r])


def test_met_verdict_needs_citation():
    with pytest.raises(ValidationError):
        RequirementVerdict(requirement_id="r_1", status=VerdictStatus.MET, rationale="x")
    RequirementVerdict(requirement_id="r_1", status=VerdictStatus.UNKNOWN, rationale="근거 부족")


def test_not_recommended_needs_alternative():
    trace = GradeTrace(rule_id="G1", inputs={"knockout_unmet": 1})
    with pytest.raises(ValidationError):
        Diagnosis(grade=Grade.NOT_RECOMMENDED, grade_trace=trace, verdicts=[])
    Diagnosis(
        grade=Grade.NOT_RECOMMENDED,
        grade_trace=trace,
        verdicts=[],
        alternatives=[Alternative(text="Data Engineer 공고", citations=[CITE])],
    )


def test_supported_claim_check_needs_evidence():
    with pytest.raises(ValidationError):
        ClaimCheck(
            claim_id="c_1",
            fact_id="f_1",
            claim="x",
            result=ClaimResult.SUPPORTED,
            method=CheckMethod.RULE,
        )


def test_reconciliation_has_no_false_claim_option_and_validates_choice():
    # ADR-0017: 사실이 아닌 기여를 쓰는 선택지는 존재하지 않는다.
    assert {c.value for c in ReconcileChoice} == {
        "revise_to_fact",
        "implement_then_reaudit",
        "add_evidence",
        "keep_as_is",
        "later",
    }
    with pytest.raises(ValidationError):
        ReconciliationDecision(
            claim_id="c_1",
            mismatch=Mismatch.CONTRIBUTION_OVERSTATED,
            options_offered=[ReconcileChoice.REVISE_TO_FACT],
            choice=ReconcileChoice.KEEP_AS_IS,
        )
    with pytest.raises(ValidationError):
        ReconciliationDecision(
            claim_id="c_1",
            mismatch=Mismatch.CONTRIBUTION_OVERSTATED,
            options_offered=[ReconcileChoice.REVISE_TO_FACT],
            choice=ReconcileChoice.REVISE_TO_FACT,
        )


def test_roadmap_item_requires_requirements_and_done_criteria():
    done = DoneCriterion(signal_path="tests.exists", op="==", value=True)
    with pytest.raises(ValidationError):
        RoadmapItem(
            kind=RoadmapKind.NEW_PROJECT,
            title="t",
            problem="p",
            done_criteria=[done],
            effort_days=2,
            priority=1,
            addresses_requirements=[],
        )
    with pytest.raises(ValidationError):
        RoadmapItem(
            kind=RoadmapKind.IMPROVE_REPO,
            title="t",
            problem="p",
            done_criteria=[done],
            effort_days=2,
            priority=1,
            addresses_requirements=["r_1"],
        )


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Citation(kind="fact", ref_id="f_1", confidence=0.9)


def test_llm_drafts_ignore_extra_fields_but_domain_models_reject_them():
    from rigor.nodes.jd_parser import JobDraft

    draft = JobDraft.model_validate(
        {
            "company": "c",
            "title": "t",
            "requirements": [{"text": "x", "kind": "required"}],
            "categories": {},
        }
    )
    assert not hasattr(draft, "categories")
    with pytest.raises(ValidationError):
        Citation(kind="fact", ref_id="f_1", extra_field=1)
