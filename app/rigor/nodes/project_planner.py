"""E5·E6: 로드맵 = 기존 저장소 개선 항목(규칙) + 신규 프로젝트(LLM 제안 + 규칙 검증).

모든 항목은 근거 요건을 인용하고, 재감사로 확인 가능한 완료 기준을 가진다 (지표: 로드맵 근거 연결률).
사용자 가용 시간 안에서 효과 대비 비용이 높은 것부터 고른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from pydantic import Field

from rigor.llm.client import LLMClient
from rigor.prompts import data_block, load_prompt
from rigor.schemas.common import DraftModel
from rigor.schemas.diagnosis import Gap, GapType, Roadmap, RoadmapItem, RoadmapKind, Severity
from rigor.schemas.job import JobPosting
from rigor.schemas.profile import Profile
from rigor.schemas.repo import DoneCriterion, Impact, Improvement

DEFAULT_BUDGET_DAYS = 21.0
# 관례값: 한 번에 실행할 수 있는 분량. 실제 리포트에서 26개 항목이 나와 읽을 수 없었다
MAX_ITEMS_PER_REPO = 3
MAX_ROADMAP_ITEMS = 8
NEW_PROJECT_PATHS = {
    "readme.sections",
    "tests.file_count",
    "ci.exists",
    "deploy.homepage_url",
    "dependencies",
}
_SEVERITY_W = {Severity.CRITICAL: 3.0, Severity.MAJOR: 2.0, Severity.MINOR: 1.0}
_IMPACT_W = {Impact.HIGH: 3.0, Impact.MED: 2.0, Impact.LOW: 1.0}


class ProjectDraft(DraftModel):
    title: str
    problem: str
    tech: list[str] = Field(default_factory=list)
    done_criteria: list[DoneCriterion] = Field(default_factory=list)
    resume_line_example: str | None = None
    effort_days: float = Field(gt=0)
    addresses_requirements: list[str] = Field(default_factory=list)


class ProjectsDraft(DraftModel):
    items: list[ProjectDraft]


@dataclass
class PlanResult:
    roadmap: Roadmap
    warnings: list[str] = field(default_factory=list)
    budget_days: float = DEFAULT_BUDGET_DAYS


def budget_days(profile: Profile, today: date | None = None) -> float:
    """마감일·주당 가용 시간이 있으면 그 안에서 (하루 = 6시간 작업 가정, 관례값)."""
    if profile.deadline and profile.available_hours_per_week:
        weeks = max(0.0, ((profile.deadline - (today or date.today())).days) / 7)
        return round(weeks * profile.available_hours_per_week / 6, 1)
    return DEFAULT_BUDGET_DAYS


def plan_new_projects(
    client: LLMClient, job: JobPosting, gaps: list[Gap], budget: float
) -> tuple[list[RoadmapItem], list[str]]:
    targets = [
        g
        for g in gaps
        if g.type is GapType.MISSING_FACT and g.severity in (Severity.CRITICAL, Severity.MAJOR)
    ]
    if not targets:
        return [], []
    prompt = load_prompt("roadmap_write")
    lines = [
        f"[{g.requirement_id}] ({g.severity.value}) {job.requirement(g.requirement_id).text}"
        for g in targets
    ]
    lines.append(f"사용 가능한 기간: 약 {budget:g}일")
    result = client.structured(
        "roadmap_write",
        system=prompt.text,
        messages=[{"role": "user", "content": data_block("gaps", "\n".join(lines))}],
        schema=ProjectsDraft,
    )
    allowed = {g.requirement_id for g in targets}
    items, warnings = [], []
    for d in result.value.items:
        addresses = [r for r in d.addresses_requirements if r in allowed]
        criteria = [c for c in d.done_criteria if c.signal_path in NEW_PROJECT_PATHS]
        if len(criteria) != len(d.done_criteria):
            warnings.append(f"'{d.title}': unverifiable done criteria dropped")
        if not addresses or not criteria:
            warnings.append(f"'{d.title}': dropped (no valid requirement or done criterion)")
            continue
        items.append(
            RoadmapItem(
                kind=RoadmapKind.NEW_PROJECT,
                title=d.title,
                problem=d.problem,
                tech=d.tech,
                done_criteria=criteria,
                resume_line_example=d.resume_line_example,
                effort_days=d.effort_days,
                priority=1,
                addresses_requirements=addresses,
            )
        )
    return items, warnings


def assemble_roadmap(
    improvements: dict[int, list[Improvement]],
    new_projects: list[RoadmapItem],
    gaps: list[Gap],
    budget: float,
) -> Roadmap:
    severity = {g.requirement_id: g.severity for g in gaps}
    scored: list[tuple[float, float, RoadmapItem]] = []
    for repo_id, imps in improvements.items():
        ranked = sorted(imps, key=lambda i: (-_IMPACT_W[i.impact], i.effort_days))
        for imp in ranked[:MAX_ITEMS_PER_REPO]:
            reqs = [c.ref_id for c in imp.citations if c.kind == "requirement"]
            if not reqs:
                continue
            item = RoadmapItem(
                kind=RoadmapKind.IMPROVE_REPO,
                title=imp.title,
                problem=imp.description,
                done_criteria=imp.done_criteria,
                effort_days=imp.effort_days,
                priority=1,
                addresses_requirements=reqs,
                repo_id=repo_id,
            )
            scored.append((_IMPACT_W[imp.impact], imp.effort_days, item))
    for p in new_projects:
        w = max(
            _SEVERITY_W.get(severity.get(r, Severity.MINOR), 1.0) for r in p.addresses_requirements
        )
        scored.append((w, p.effort_days, p))
    scored.sort(key=lambda x: (-x[0], x[1]))  # 효과 먼저, 같으면 싼 것부터
    chosen, used = [], 0.0
    for _, _, item in scored:
        if len(chosen) >= MAX_ROADMAP_ITEMS or used + item.effort_days > budget:
            continue
        used += item.effort_days
        chosen.append(item.model_copy(update={"priority": len(chosen) + 1}))
    return Roadmap(items=chosen)
