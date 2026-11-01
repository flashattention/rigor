"""합성 케이스의 정답에서 "올바른 LLM 출력"을 만든다.

FakeAdapter에 등록하면 실제 LLM 없이 노드의 후처리·배선을 끝까지 테스트할 수 있다.
오라클은 정답을 아는 이상적인 모델이므로, 이걸로 얻은 점수는 파이프라인의 상한선이다.
"""

from __future__ import annotations

from rigor.ingest.privacy import MaskResult
from rigor.llm.adapters.fake import FakeAdapter
from rigor.nodes.jd_parser import JobDraft, RequirementDraft
from rigor.nodes.profile_builder import ClaimDraft, FactDraft, ProfileDraft
from rigor.schemas.profile import FactCategory
from rigor.synth.spec import Case


def _ym(d) -> str | None:
    return d.strftime("%Y-%m") if d else None


def profile_draft(case: Case, mask: MaskResult) -> ProfileDraft:
    url_to_token = {url: token for token, url in mask.repo_urls.items()}
    facts = []
    for f in case.profile.facts:
        refs = []
        if f.id in case.linked_in_resume:
            refs.append(url_to_token[case.repo(case.linked_in_resume[f.id]).url])
        facts.append(
            FactDraft(
                category=f.category,
                content=f.content,
                start=_ym(f.period.start),
                end=_ym(f.period.end),
                ongoing=f.period.ongoing,
                organization=f.organization if f.category is not FactCategory.EDUCATION else None,
                role=f.role,
                attribution=f.attribution,
                skills=f.skills,
                metrics=f.metrics,
                repo_refs=refs,
                education=f.education_normalized,
                claims=[ClaimDraft(type=c.type, text=c.text) for c in f.claims],
            )
        )
    return ProfileDraft(facts=facts)


def job_draft(case: Case) -> JobDraft:
    job = case.job
    return JobDraft(
        company=job.company,
        title=job.title,
        role_ids=job.role_ids,
        language=job.language,
        blind=job.blind,
        experience_min_years=job.experience_years.min,
        responsibilities=job.responsibilities,
        requirements=[
            RequirementDraft(
                text=r.text,
                kind=r.kind,
                knockout_type=r.knockout_type,
                competencies=r.competencies,
                skills=r.skills,
                min_depth=r.min_depth,
            )
            for r in job.requirements
        ],
    )


def oracle_adapter(case: Case, mask: MaskResult) -> FakeAdapter:
    return (
        FakeAdapter()
        .on("fact_extract", lambda _req: profile_draft(case, mask))
        .on("jd_parse", lambda _req: job_draft(case))
    )
