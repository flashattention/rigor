"""합성 케이스의 정답에서 "올바른 LLM 출력"을 만든다.

FakeAdapter에 등록하면 실제 LLM 없이 노드의 후처리·배선을 끝까지 테스트할 수 있다.
오라클은 정답을 아는 이상적인 모델이므로, 이걸로 얻은 점수는 파이프라인의 상한선이다.
"""

from __future__ import annotations

import re

from rigor.ingest.privacy import MaskResult
from rigor.llm.adapters.fake import FakeAdapter
from rigor.nodes.jd_parser import JobDraft, RequirementDraft
from rigor.nodes.profile_builder import ClaimDraft, FactDraft, ProfileDraft
from rigor.nodes.project_planner import ProjectDraft, ProjectsDraft
from rigor.nodes.reconcile import CardDraft, CardsDraft
from rigor.nodes.requirement_matcher import CitationDraft, VerdictDraft, VerdictsDraft
from rigor.schemas.profile import FactCategory
from rigor.schemas.repo import DoneCriterion
from rigor.synth.spec import Case


def _ym(d) -> str | None:
    return d.strftime("%Y-%m") if d else None


def profile_draft(case: Case, mask: MaskResult) -> ProfileDraft:
    """케이스의 fact를 LLM 초안 형태로 되돌린다. 저장소 URL은 마스킹 토큰으로 적는다."""
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


def verdicts_draft(case: Case) -> VerdictsDraft:
    return VerdictsDraft(
        verdicts=[
            VerdictDraft(
                requirement_id=rid,
                status=status,
                citations=[
                    CitationDraft(ref_id=f) for f in case.truth.verdict_support.get(rid, [])
                ],
                rationale="합성 정답에 따른 판정",
            )
            for rid, status in case.truth.verdicts.items()
        ]
    )


def cards_draft(request_text: str) -> CardsDraft:
    """요청 문구에 나온 claim id마다 고정 문구의 카드를 만든다."""
    ids = dict.fromkeys(re.findall(r"\[(c_[A-Za-z0-9_]+)\]", request_text))
    return CardsDraft(
        cards=[
            CardDraft(
                claim_id=cid,
                likely_questions=["이 부분에서 본인이 내린 설계 결정은 무엇인가요?"],
                talking_points=["저장소에서 확인된 근거를 짚는다"],
            )
            for cid in ids
        ]
    )


def projects_draft(request_text: str) -> ProjectsDraft:
    """요청 문구에 나온 요건 id마다 고정 문구의 프로젝트를 만든다."""
    ids = dict.fromkeys(re.findall(r"\[(r_[0-9]+)\]", request_text))
    return ProjectsDraft(
        items=[
            ProjectDraft(
                title=f"{rid} 근거를 만드는 소규모 프로젝트",
                problem="부족한 요건을 직접 구현해 근거를 만든다",
                done_criteria=[
                    DoneCriterion(signal_path="tests.file_count", op=">=", value=3),
                    DoneCriterion(signal_path="readme.sections", op="contains", value="results"),
                ],
                resume_line_example="(완료 후) 요건과 관련된 기능을 구현하고 평가 결과를 README에 게시",
                effort_days=5,
                addresses_requirements=[rid],
            )
            for rid in ids
        ]
    )


def oracle_adapter(case: Case, mask: MaskResult | None = None) -> FakeAdapter:
    """정답을 돌려주는 FakeAdapter. mask가 없으면 fact_extract는 등록하지 않는다."""

    def text(req) -> str:
        return "".join(m["content"] for m in req.messages)

    adapter = (
        FakeAdapter()
        .on("jd_parse", lambda _req: job_draft(case))
        .on("requirement_verdict", lambda _req: verdicts_draft(case))
        .on("interview_card", lambda req: cards_draft(text(req)))
        .on("roadmap_write", lambda req: projects_draft(text(req)))
    )
    if mask is not None:
        adapter.on("fact_extract", lambda _req: profile_draft(case, mask))
    return adapter
