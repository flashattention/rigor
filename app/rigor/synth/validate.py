"""정답과 데이터가 정말 일치하는지 확인한다. 정답이 틀리면 평가 전체가 틀리기 때문이다.

문제가 있으면 문자열 목록으로 돌려준다 (빈 목록 = 일관됨).
"""

from __future__ import annotations

from rigor.schemas.diagnosis import Grade, VerdictStatus
from rigor.schemas.job import RequirementKind
from rigor.schemas.profile import UnlinkedReason
from rigor.schemas.repo import ClaimResult, ExposureKind
from rigor.synth.render import render_resume
from rigor.synth.spec import Case


def _repo_text(case: Case, repo_id: int) -> str:
    return "\n".join(case.repo(repo_id).final_files().values()).lower()


def validate_case(case: Case) -> list[str]:
    problems: list[str] = []
    t = case.truth
    repo_ids = {r.repo_id for r in case.repos}
    facts = {f.id: f for f in case.profile.facts}
    claims = {c.id: c for f in case.profile.facts for c in f.claims}

    # 요건 판정 정답은 공고 요건과 1:1
    req_ids = {r.id for r in case.job.requirements}
    if set(t.verdicts) != req_ids:
        problems.append(f"verdict keys {sorted(t.verdicts)} != requirements {sorted(req_ids)}")

    # 결격 미충족이면 비추천, 비추천이면 대안 필요
    knockouts = [r.id for r in case.job.requirements if r.kind is RequirementKind.KNOCKOUT]
    if (
        any(t.verdicts.get(r) is VerdictStatus.UNMET for r in knockouts)
        and t.grade is not Grade.NOT_RECOMMENDED
    ):
        problems.append("knockout unmet but grade is not not_recommended")
    if t.grade is Grade.NOT_RECOMMENDED and not t.alternative_expected:
        problems.append("not_recommended case must expect an alternative path")

    # 모든 claim에 정답이 있고, 정답은 존재하는 claim만
    truth_ids = {c.claim_id for c in t.claims}
    if truth_ids != set(claims):
        problems.append(f"claim truths {sorted(truth_ids ^ set(claims))} mismatch claims")

    # 링크·미연결 정합
    for fid, rid in case.linked_in_resume.items():
        if fid not in facts or rid not in repo_ids:
            problems.append(f"linked_in_resume {fid}->{rid} refers to missing fact/repo")
    for u in t.unlinked:
        if u.fact_id in case.linked_in_resume:
            problems.append(f"{u.fact_id} is marked unlinked but has a link in the resume")
        if u.reason is UnlinkedReason.FORGOT and u.correct_repo_id not in repo_ids:
            problems.append(f"{u.fact_id} forgot without a valid correct_repo_id")
    resume = render_resume(case)
    for rid in case.linked_in_resume.values():
        if case.repo(rid).url not in resume:
            problems.append(f"resume is missing url for repo {rid}")
    for u in t.unlinked:
        if u.correct_repo_id and case.repo(u.correct_repo_id).url in resume:
            problems.append(f"resume leaks url of unlinked repo {u.correct_repo_id}")

    # tech·metric claim의 키워드가 저장소에 정답대로 있거나 없다
    fact_repo = dict(case.linked_in_resume)
    fact_repo.update({u.fact_id: u.correct_repo_id for u in t.unlinked if u.correct_repo_id})
    for ct in t.claims:
        if ct.keyword is None:
            continue
        rid = fact_repo.get(claims[ct.claim_id].fact_id)
        if rid is None:
            problems.append(f"{ct.claim_id} has a keyword but its fact has no repo")
            continue
        present = ct.keyword.lower() in _repo_text(case, rid)
        if ct.result is ClaimResult.SUPPORTED and not present:
            problems.append(f"{ct.claim_id} supported but '{ct.keyword}' absent in repo {rid}")
        if ct.result is ClaimResult.NOT_FOUND and present:
            problems.append(f"{ct.claim_id} not_found but '{ct.keyword}' present in repo {rid}")
        if ct.result is not ClaimResult.SUPPORTED and ct.mismatch is None:
            problems.append(f"{ct.claim_id} is {ct.result} without a mismatch type")

    # 기여 비율·AI 공동 작성 수는 커밋 명세에서 계산한 값과 같아야 한다
    for rt in t.repos:
        if rt.repo_id not in repo_ids:
            problems.append(f"repo truth for missing repo {rt.repo_id}")
            continue
        repo = case.repo(rt.repo_id)
        share = repo.user_commit_count() / len(repo.commits)
        if abs(share - rt.user_share) > 1e-9:
            problems.append(f"repo {rt.repo_id} user_share {rt.user_share} != computed {share}")
        account = set(case.profile.identity.emails[:1]) if case.profile.identity else set()
        if rt.user_share_without_aliases is not None:
            only_account = sum(1 for c in repo.commits if repo.author(c.author).email in account)
            if abs(only_account / len(repo.commits) - rt.user_share_without_aliases) > 1e-9:
                problems.append(f"repo {rt.repo_id} user_share_without_aliases mismatch")
        ai = sum(1 for c in repo.commits if c.ai_coauthored)
        if ai != rt.ai_coauthored_commits:
            problems.append(f"repo {rt.repo_id} ai_coauthored {rt.ai_coauthored_commits} != {ai}")
    if {rt.repo_id for rt in t.repos} != repo_ids:
        problems.append("every repo needs a repo truth")

    # 심은 노출이 실제로 파일에 있다
    for e in t.exposures:
        content = case.repo(e.repo_id).final_files().get(e.path, "")
        needle = {
            ExposureKind.SECRET: "aws_secret_access_key",
            ExposureKind.REAL_NAME: case.header.name.lower(),
            ExposureKind.CONTACT: case.header.phone,
        }.get(e.kind)
        if needle and needle not in content.lower():
            problems.append(f"planted {e.kind} not found in {e.repo_id}:{e.path}")

    # 보호 속성은 프로필 fact에 새지 않는다 (헤더에만 있어야 함)
    profile_text = case.profile.model_dump_json().lower()
    for leak in (case.header.name, case.header.school, case.header.phone):
        if leak.lower() in profile_text:
            problems.append(f"header value '{leak}' leaked into profile facts")
    return problems
