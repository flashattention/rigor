import pytest

from rigor.nodes.requirement_matcher import _degree_verdict
from rigor.schemas.diagnosis import VerdictStatus
from rigor.schemas.profile import (
    Degree,
    EducationNormalized,
    EducationStatus,
    Fact,
    FactCategory,
    FactStatus,
    Profile,
)


def profile(degree: Degree, status: EducationStatus) -> Profile:
    fact = Fact(
        id="f_1",
        category=FactCategory.EDUCATION,
        content="학력",
        status=FactStatus.CONFIRMED,
        education_normalized=EducationNormalized(degree=degree, status=status),
    )
    return Profile(id="p", facts=[fact])


GRADUATED = EducationStatus.GRADUATED


@pytest.mark.parametrize(
    ("req", "degree", "expected"),
    [
        ("학사 이상 (석사 우대)", Degree.BACHELOR, VerdictStatus.MET),
        ("학사 또는 석사", Degree.BACHELOR, VerdictStatus.MET),
        ("석사 이상", Degree.BACHELOR, VerdictStatus.UNMET),
        ("전문학사 이상", Degree.ASSOCIATE, VerdictStatus.MET),
        ("학사 이상", Degree.ASSOCIATE, VerdictStatus.UNMET),
        ("초대졸 이상", Degree.ASSOCIATE, VerdictStatus.MET),
    ],
)
def test_required_degree_is_the_lowest_one_mentioned(req, degree, expected):
    assert _degree_verdict(req, profile(degree, GRADUATED)).status is expected


def test_dropping_out_does_not_count_as_holding_the_degree():
    v = _degree_verdict("학사 이상", profile(Degree.BACHELOR, EducationStatus.DROPPED))
    assert v.status is VerdictStatus.UNMET


@pytest.mark.parametrize("status", [EducationStatus.ENROLLED, EducationStatus.EXPECTED])
def test_degree_in_progress_is_left_to_the_llm(status):
    # "졸업 예정자 가능" 같은 문구는 공고마다 다르므로 규칙으로 단정하지 않는다
    assert _degree_verdict("학사 이상", profile(Degree.BACHELOR, status)) is None


def test_lower_degree_in_progress_does_not_hide_a_clear_unmet():
    v = _degree_verdict("석사 이상", profile(Degree.BACHELOR, EducationStatus.ENROLLED))
    assert v.status is VerdictStatus.UNMET
