import subprocess

import pytest

from rigor.synth.cases import (
    INJECTION,
    base_cases,
    case_ai_and_exposure,
    case_contribution_overstated,
)
from rigor.synth.gitrepo import materialize
from rigor.synth.render import render_jd, render_resume
from rigor.synth.validate import validate_case
from rigor.synth.variants import all_variants, counterfactuals, inject

ALL = base_cases() + all_variants(base_cases())


@pytest.mark.parametrize("case", ALL, ids=lambda c: c.case_id)
def test_every_case_is_internally_consistent(case):
    assert validate_case(case) == []


def test_generation_is_deterministic():
    first = [c.model_dump_json() for c in base_cases()]
    second = [c.model_dump_json() for c in base_cases()]
    assert first == second


def test_case_ids_are_unique():
    ids = [c.case_id for c in ALL]
    assert len(ids) == len(set(ids))


def git_log(path, fmt):
    out = subprocess.run(
        ["git", "log", f"--format={fmt}"], cwd=path, check=True, capture_output=True, text=True
    )
    return out.stdout


def test_materialized_repo_keeps_authors_aliases_and_dates(tmp_path):
    case = case_contribution_overstated()
    repo = case.repo(401)
    path = materialize(repo, tmp_path)
    emails = git_log(path, "%ae").split()
    assert len(emails) == 30
    user_emails = set(case.profile.identity.emails)
    assert sum(e in user_emails for e in emails) == 4
    assert sum(e == case.profile.identity.emails[0] for e in emails) == 2
    dates = git_log(path, "%aI").split()
    assert dates[-1].startswith("2025-04-01")


def test_materialized_repo_has_ai_trailers_and_planted_secret(tmp_path):
    case = case_ai_and_exposure()
    path = materialize(case.repo(501), tmp_path)
    bodies = git_log(path, "%B")
    assert bodies.count("Co-Authored-By: Claude") == 9
    assert "AWS_SECRET_ACCESS_KEY" in (path / "app/config.py").read_text()
    assert case.header.name in (path / "README.md").read_text()


def test_resume_shows_links_only_for_linked_facts():
    case = base_cases()[0]
    resume = render_resume(case)
    assert case.repo(101).url in resume and case.repo(102).url in resume
    assert case.repo(103).url not in resume  # 실수로 안 적은 저장소
    assert case.header.school in resume


def test_counterfactuals_change_only_the_header():
    base = case_ai_and_exposure()
    variants = counterfactuals(base)
    assert len(variants) == 8
    for v in variants:
        assert v.truth == base.truth
        assert v.profile == base.profile
        assert v.job == base.job
    resumes = {render_resume(v) for v in variants}
    assert len(resumes) == 8
    # 저장소에 심은 실명은 새 이름으로 바뀐다
    assert variants[0].header.name in variants[0].repo(501).final_files()["README.md"]


@pytest.mark.parametrize("site", ["resume", "jd", "readme", "code_comment"])
def test_injection_lands_at_site_without_changing_truth_or_commits(site):
    base = base_cases()[0]
    v = inject(base, site)
    assert v.truth == base.truth
    assert [len(r.commits) for r in v.repos] == [len(r.commits) for r in base.repos]
    text = {
        "resume": render_resume(v),
        "jd": render_jd(v),
        "readme": v.repo(101).final_files()["README.md"],
        "code_comment": v.repo(101).final_files().get("src/notes.py", ""),
    }[site]
    assert INJECTION in text


def test_jd_marks_blind_hiring():
    assert "블라인드 채용" in render_jd(case_ai_and_exposure())
