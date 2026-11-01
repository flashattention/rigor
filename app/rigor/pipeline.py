"""핵심 흐름 B→F를 순서대로 실행한다 (ADR-0017). A단계(파싱·확인)는 앞에서 끝난 상태로 들어온다.

각 단계는 순수 함수이고, 사용자 선택은 chooser로 주입된다 (CLI·웹·테스트가 각자 구현).
M2에서 LangGraph가 이 함수들을 노드로 감싸 interrupt·영속 재개를 붙인다 (ADR-0002).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from rigor.core.gaps import (
    alternatives,
    classify_gaps,
    questions,
    suggest_redirect,
    weaknesses,
)
from rigor.core.grading import GradeInputs, compute_grade
from rigor.core.improvements import improvements_for
from rigor.core.repo_verdict import RepoJudgement, judge_repo
from rigor.core.verify import EvidenceIndex, VerifyReport, keep_cited, policy_check
from rigor.github_audit.audit import audit_repo, repo_facts
from rigor.github_audit.claims import check_claims_by_rules
from rigor.github_audit.exposure import ExposureContext
from rigor.github_audit.snapshot import RepoSnapshot
from rigor.llm.client import LLMClient
from rigor.nodes.project_planner import assemble_roadmap, budget_days, plan_new_projects
from rigor.nodes.reconcile import (
    ExplainChooser,
    ReconcileChooser,
    ReconcileOutcome,
    default_choice,
    interview_cards,
    reconcile,
)
from rigor.nodes.repo_linking import (
    Chooser,
    LinkCheck,
    apply_link_checks,
    check_links,
    find_unlinked,
    resolve_unlinked,
)
from rigor.nodes.requirement_matcher import (
    cited_requirements_by_repo,
    match_requirements,
    summary_evidence,
)
from rigor.schemas.diagnosis import Diagnosis, Roadmap
from rigor.schemas.job import JobPosting
from rigor.schemas.profile import Profile
from rigor.schemas.repo import (
    ClaimCheck,
    Improvement,
    InterviewCard,
    RepoAudit,
    RepoVerdict,
    UnlinkedItem,
)


@dataclass
class Choosers:
    unlinked: Chooser
    reconcile: ReconcileChooser = default_choice
    can_explain: ExplainChooser = lambda _f: True


@dataclass
class ResumeAction:
    fact_id: str
    content: str
    action: str


@dataclass
class DiagnosisReport:
    job: JobPosting
    profile: Profile  # B단계 후 (링크 반영)
    evidence_profile: Profile  # D단계 후 (판정 근거)
    link_checks: list[LinkCheck]
    unlinked: list[UnlinkedItem]
    audits: dict[int, RepoAudit]
    no_repo_checks: list[ClaimCheck]
    reconcile: ReconcileOutcome
    cards: list[InterviewCard]
    judgements: list[RepoJudgement]
    grade_inputs: GradeInputs
    diagnosis: Diagnosis
    improvements: dict[int, list[Improvement]]
    roadmap: Roadmap
    resume_actions: list[ResumeAction]
    verify: VerifyReport
    budget_days: float
    warnings: list[str] = field(default_factory=list)
    prompt_ids: list[str] = field(default_factory=list)
    routing_hash: str = ""
    cost_usd: float = 0.0


def run_pipeline(
    profile: Profile,
    job: JobPosting,
    snapshots: dict[int, RepoSnapshot],
    client: LLMClient,
    exposure: ExposureContext,
    choosers: Choosers,
    today: date | None = None,
) -> DiagnosisReport:
    identity = profile.identity
    if identity is None:
        raise ValueError("commit identity must be confirmed before stage B (A6)")
    warnings: list[str] = []

    # B: 저장소 연결
    catalog = [repo_facts(s, identity) for s in snapshots.values()]
    link_checks = check_links(profile, catalog)
    profile = apply_link_checks(profile, link_checks)
    profile, unlinked = resolve_unlinked(
        profile, find_unlinked(profile, catalog), catalog, choosers.unlinked
    )

    # C: 저장소 검증 (규칙 단계). 보류 claim은 M1b의 근거 검색 + LLM으로 넘어간다.
    audits: dict[int, RepoAudit] = {}
    pending = 0
    for rid, snap in snapshots.items():
        facts = [f for f in profile.facts if f.repo_link and f.repo_link.repo_id == rid]
        result = audit_repo(snap, identity, facts, exposure)
        audits[rid] = result.audit
        pending += len(result.pending)
    if pending:
        warnings.append(
            f"규칙으로 판단하지 못한 claim {pending}개는 이번 버전에서 검증하지 않았다 (M1b 예정)"
        )
    no_repo_checks = [
        c
        for f in profile.facts
        if f.claims and not (f.repo_link and f.repo_link.repo_id in audits)
        for c in check_claims_by_rules(f, None, None, None).checks
    ]

    # D: 정합
    outcome = reconcile(profile, audits, choosers.reconcile, choosers.can_explain)
    evidence = outcome.evidence_profile
    cards, card_warnings = interview_cards(client, evidence, audits)
    warnings += card_warnings

    # E1 요건 판정 → E4 저장소 판정 → E2 등급 → E3 진단
    match, bundle = match_requirements(client, job, evidence, audits)
    warnings += match.warnings
    cited = cited_requirements_by_repo(match.verdicts, bundle)
    judgements = sorted(
        (judge_repo(audits[rid], snapshots[rid], job, cited.get(rid, set())) for rid in audits),
        key=lambda j: j.order_key,
    )
    for j in judgements:
        audits[j.repo_id] = audits[j.repo_id].model_copy(
            update={"verdict_for_jd": j.verdict, "rubric_scores": j.scores}
        )
    showcase_reqs = {
        r for j in judgements if j.verdict is RepoVerdict.SHOWCASE for r in j.relevant_requirements
    }
    grade, trace, gi = compute_grade(job, match.verdicts, showcase_reqs)
    redirect = suggest_redirect(job, grade, evidence, audits, judgements)

    index = EvidenceIndex(
        requirements={r.id for r in job.requirements},
        facts={f.id for f in profile.facts},
        repo_evidence={e.id for a in audits.values() for e in a.evidence}
        | {summary_evidence(a).id for a in audits.values()},
    )
    report = VerifyReport()
    gaps = keep_cited(classify_gaps(job, match.verdicts, audits), index, report, "gap")
    weak = keep_cited(weaknesses(profile, audits, judgements), index, report, "weakness")
    redirect = (
        (keep_cited([redirect], index, report, "redirect") or [None])[0] if redirect else None
    )
    alts = keep_cited(alternatives(job, grade, gi, redirect), index, report, "alternative")
    diagnosis = Diagnosis(
        grade=grade,
        grade_trace=trace,
        verdicts=match.verdicts,
        gaps=gaps,
        weaknesses=weak,
        redirect=redirect,
        alternatives=alts,
        questions_for_user=questions(job, match.verdicts),
    )

    # E5·E6 로드맵, E7 이력서 배치
    improvements = {j.repo_id: improvements_for(audits[j.repo_id], j) for j in judgements}
    improvements = {k: keep_cited(v, index, report, "improvement") for k, v in improvements.items()}
    budget = budget_days(evidence, today)
    new_projects, plan_warnings = plan_new_projects(client, job, gaps, budget)
    warnings += plan_warnings
    roadmap = assemble_roadmap(improvements, new_projects, gaps, budget)
    resume_actions = _resume_actions(profile, judgements, unlinked)

    # F2 정책 검사: 생성 문장에 보호 속성·식별 정보가 섞이지 않았는지
    texts = [v.rationale for v in match.verdicts] + [w.text for w in weak] + [a.text for a in alts]
    texts += [
        i.title + " " + i.problem + " " + (i.resume_line_example or "") for i in roadmap.items
    ]
    texts += [q for c in cards for q in [*c.likely_questions, *c.talking_points]]
    policy_check(
        texts, [*exposure.names, *exposure.phones, *exposure.emails, *exposure.schools], report
    )

    return DiagnosisReport(
        job=job,
        profile=profile,
        evidence_profile=evidence,
        link_checks=link_checks,
        unlinked=unlinked,
        audits=audits,
        no_repo_checks=no_repo_checks,
        reconcile=outcome,
        cards=cards,
        judgements=judgements,
        grade_inputs=gi,
        diagnosis=diagnosis,
        improvements=improvements,
        roadmap=roadmap,
        resume_actions=resume_actions,
        verify=report,
        budget_days=budget,
        warnings=warnings,
        prompt_ids=[match.prompt_id],
        routing_hash=client.routing.hash,
        cost_usd=client.meter.total_usd,
    )


def _resume_actions(
    profile: Profile, judgements: list[RepoJudgement], unlinked: list[UnlinkedItem]
) -> list[ResumeAction]:
    by_repo = {j.repo_id: j for j in judgements}
    proposals = {u.fact_id: u.proposal for u in unlinked}
    label = {
        RepoVerdict.SHOWCASE: "이력서 상단에 대표 프로젝트로 배치",
        RepoVerdict.IMPROVE: "보완 항목을 처리한 뒤 유지",
        RepoVerdict.DROP: "이 공고에서는 빼거나 한 줄로 축소",
    }
    ranked: list[tuple[tuple[int, float], ResumeAction]] = []
    for f in profile.facts:
        rid = f.repo_link.repo_id if f.repo_link else None
        if rid in by_repo:
            j = by_repo[rid]
            ranked.append((j.order_key, ResumeAction(f.id, f.content, label[j.verdict])))
        elif f.id in proposals and proposals[f.id] is not None:
            text = {
                "add_evidence_or_reduce": "저장소가 없다 — 데모·글 등 근거를 붙이거나 비중을 줄일 것",
                "keep_with_interview_prep": "유지하되 면접에서 설명할 준비 (저장소로 확인 불가)",
            }.get(proposals[f.id].value, proposals[f.id].value)
            ranked.append(((1, 0.0), ResumeAction(f.id, f.content, text)))
    return [a for _, a in sorted(ranked, key=lambda x: x[0])]
