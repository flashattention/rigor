"""F3: Markdown 리포트. 모든 지적에 근거가 붙고, 한계 고지가 항상 들어간다."""

from __future__ import annotations

from rigor.pipeline import DiagnosisReport
from rigor.schemas.common import Citation
from rigor.schemas.diagnosis import Grade, VerdictStatus
from rigor.schemas.profile import FactCategory
from rigor.schemas.repo import ClaimResult, RepoVerdict

GRADE_LABEL = {
    Grade.STRONG: "강력 추천",
    Grade.APPLY: "지원 가능",
    Grade.GAP_FIRST: "갭 보완 후",
    Grade.NOT_RECOMMENDED: "비추천",
}
RULE_TEXT = {
    "G1": "결격 요건을 충족하지 않음",
    "G2": "필수 요건 충족률 40% 미만",
    "G3": "직무 핵심 역량 요건에 근거 없음",
    "G4": "필수 요건 충족률 60% 미만",
    "G5": "필수 요건에 빈칸 없음 + 핵심 요건 근거 완전 + 대표 저장소 있음",
    "G6": "필수 요건 충족률 60% 이상, 강력 추천 조건은 미충족",
}
STATUS_LABEL = {
    VerdictStatus.MET: "충족",
    VerdictStatus.PARTIAL: "부분",
    VerdictStatus.UNMET: "미충족",
    VerdictStatus.UNKNOWN: "판단 보류",
}
VERDICT_LABEL = {
    RepoVerdict.SHOWCASE: "대표로 쓸 것",
    RepoVerdict.IMPROVE: "보완 후 사용",
    RepoVerdict.DROP: "이 공고에서는 제외",
}
CLAIM_LABEL = {
    ClaimResult.SUPPORTED: "확인됨",
    ClaimResult.NOT_FOUND: "근거 없음",
    ClaimResult.CONTRADICTED: "근거와 다름",
    ClaimResult.UNVERIFIABLE: "저장소로 확인 불가",
}


def _cites(cites: list[Citation]) -> str:
    return ", ".join(f"`{c.ref_id}`" for c in cites) or "—"


def render_markdown(r: DiagnosisReport) -> str:
    d = r.diagnosis
    job = r.job
    out: list[str] = []
    out.append(f"# RIGOR 진단 리포트 — {job.company} · {job.title}")
    out.append("")
    out.append(
        "> AI가 생성한 결과입니다. 실제 채용 결과를 보장하지 않으며, 회사 내부 기준은 알 수 없습니다. "
        "합격 확률이 아니라 근거 기반 등급입니다 (보정할 실제 결과 데이터가 아직 없기 때문)."
    )
    out.append("")
    out.append(f"## 등급: **{GRADE_LABEL[d.grade]}**")
    gi = r.grade_inputs
    out.append(f"- 적용 규칙 {d.grade_trace.rule_id}: {RULE_TEXT[d.grade_trace.rule_id]}")
    out.append(
        f"- 필수 요건 충족률 {gi.cov_req:.0%} (부분 충족은 0.5로 계산) · 핵심 역량 갭 {len(gi.critical_gaps)}개 · 결격 {len(gi.knockout_unmet)}개"
    )
    if d.redirect:
        out.append(
            f"- **방향 전환 제안**: {d.redirect.reason} (근거 {_cites(d.redirect.citations)})"
        )
    for a in d.alternatives:
        out.append(f"- 대안: {a.text} (근거 {_cites(a.citations)})")
    out.append("")

    out.append("## 요건별 판정")
    out.append("| 요건 | 종류 | 판정 | 근거 | 이유 |")
    out.append("|---|---|---|---|---|")
    for v in d.verdicts:
        req = job.requirement(v.requirement_id)
        out.append(
            f"| {req.id} {req.text} | {req.kind.value} | {STATUS_LABEL[v.status]} | {_cites(v.citations)} | {v.rationale} |"
        )
    for q in d.questions_for_user:
        out.append(f"- 질문: {q}")
    out.append("")

    if d.gaps or d.weaknesses:
        out.append("## 갭과 서류상 약점")
        for g in d.gaps:
            out.append(
                f"- [{g.severity.value}] {job.requirement(g.requirement_id).text} — {g.type.value}: {g.next_action}"
            )
        for w in d.weaknesses:
            out.append(f"- {w.category} {w.text} (근거 {_cites(w.citations)})")
        out.append("")

    out.append("## 이력서 ↔ 저장소 대조")
    facts = {f.id: f for f in r.profile.facts}
    for a in r.audits.values():
        for c in a.claim_checks:
            out.append(
                f"- [{CLAIM_LABEL[c.result]}] {facts[c.fact_id].content}: {c.claim}"
                + (f" — {c.action}" if c.action and c.result is not ClaimResult.SUPPORTED else "")
            )
    unverifiable: dict[str, int] = {}
    for c in r.no_repo_checks:
        if facts[c.fact_id].category in (FactCategory.PROJECT, FactCategory.EXPERIENCE):
            unverifiable[facts[c.fact_id].content] = (
                unverifiable.get(facts[c.fact_id].content, 0) + 1
            )
    for content, n in unverifiable.items():
        out.append(
            f"- [{CLAIM_LABEL[ClaimResult.UNVERIFIABLE]}] {content}: 주장 {n}개 — 면접에서 설명할 준비"
        )
    if r.reconcile.decisions:
        out.append("")
        out.append("### 정합 결과")
        for dec in r.reconcile.decisions:
            fix = f' → "{dec.revised_text}"' if dec.revised_text else ""
            out.append(
                f"- {dec.claim_id} ({dec.mismatch.value}): {dec.choice.value if dec.choice else '미정'}{fix}"
            )
    for line in r.reconcile.differs_from_evidence:
        out.append(f"- ⚠ 근거와 다름 (사용자가 유지 선택, 판정 근거에서 제외): {line}")
    for note in r.reconcile.ai_notes:
        out.append(f"- AI 도구 사용: {note}")
    out.append("")

    out.append("## 저장소 판정과 이력서 배치")
    others = [j for j in r.judgements if j.repo_id not in r.linked_repo_ids]
    for j in others:
        if j.verdict is RepoVerdict.SHOWCASE:
            out.append(
                f"- 이력서에 없지만 대표로 쓸 만함: **{r.audits[j.repo_id].full_name}** · 품질 {j.quality:.0%}"
            )
    if others:
        out.append(f"- 이력서에 연결되지 않은 저장소 {len(others)}개는 아래 판정에서 뺐다")
    for j in r.judgements:
        if j.repo_id not in r.linked_repo_ids:
            continue
        a = r.audits[j.repo_id]
        extra = "; ".join(j.blockers + j.notes)
        out.append(
            f"- **{a.full_name}**: {VERDICT_LABEL[j.verdict]} · 품질 {j.quality:.0%} · 관련 요건 {', '.join(j.relevant_requirements) or '없음'}"
            + (f" · {extra}" if extra else "")
            + f" · 스캔 범위 {a.scan_scope}"
        )
        for e in a.exposure_findings:
            out.append(f"  - ⚠ 노출: {e.detail} ({e.path}:{e.line})")
        if a.contribution and a.contribution.total_commits:
            c = a.contribution
            caveat = f" (주의: {', '.join(x.value for x in c.caveats)})" if c.caveats else ""
            out.append(
                f"  - 본인 커밋 {c.user_commits}/{c.total_commits}{caveat} — 판정이 아니라 면접 설명 준비용"
            )
    out.append("")
    out.append("### 이력서 순서 제안")
    for i, act in enumerate(r.resume_actions, start=1):
        out.append(f"{i}. {act.content} — {act.action}")
    out.append("")

    out.append(f"## 로드맵 (가용 기간 약 {r.budget_days:g}일)")
    for item in r.roadmap.items:
        done = "; ".join(f"{c.signal_path} {c.op} {c.value}" for c in item.done_criteria)
        where = f"{r.audits[item.repo_id].full_name}: " if item.repo_id in r.audits else ""
        out.append(
            f"{item.priority}. [{item.kind.value}] {where}{item.title} ({item.effort_days:g}일) — 요건 {', '.join(item.addresses_requirements)}"
        )
        out.append(f"   - 완료 기준(재감사로 확인): {done}")
        if item.resume_line_example:
            out.append(f"   - 완료 후 이력서 문장 예시: {item.resume_line_example}")
    out.append("")

    if r.cards:
        out.append("## 면접 준비 카드")
        claims = {c.id: c for f in r.profile.facts for c in f.claims}
        for card in r.cards:
            out.append(
                f"### {claims[card.claim_id].text if card.claim_id in claims else card.claim_id}"
            )
            out += [f"- Q. {q}" for q in card.likely_questions]
            out += [f"- 짚을 사실: {t}" for t in card.talking_points]
            if card.caution:
                out.append(f"- 주의: {card.caution}")
        out.append("")

    out.append("## 검증·한계")
    out.append(f"- 근거 없는 지적으로 제거된 항목: {r.verify.dropped_uncited}개")
    for flag in r.verify.policy_flags:
        out.append(f"- 정책 경고: {flag}")
    for w in r.warnings:
        out.append(f"- {w}")
    out.append(
        f"- 재현 정보: routing `{r.routing_hash}`, 프롬프트 {', '.join(r.prompt_ids)}, LLM 비용 ${r.cost_usd:.4f}"
    )
    return "\n".join(out) + "\n"
