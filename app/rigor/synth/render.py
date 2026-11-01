"""케이스 → 이력서·공고 원문 텍스트. Profile Builder·JD Parser의 입력이 된다."""

from __future__ import annotations

from rigor.schemas.job import RequirementKind
from rigor.schemas.profile import FactCategory, Period
from rigor.synth.spec import Case

_SECTION = {
    FactCategory.EXPERIENCE: "경력",
    FactCategory.PROJECT: "프로젝트",
    FactCategory.AWARD: "수상",
    FactCategory.CERTIFICATION: "자격",
    FactCategory.LANGUAGE: "어학",
    FactCategory.SKILL: "기술",
}


def _period(p: Period) -> str:
    def fmt(d):
        return d.strftime("%Y.%m") if d else "?"

    if not (p.start or p.end):
        return ""
    end = "현재" if p.ongoing else fmt(p.end)
    return f" ({fmt(p.start)} ~ {end})"


def render_resume(case: Case) -> str:
    """이력서 원문을 만든다. 저장소 링크는 linked_in_resume에 있는 fact에만 적는다."""
    h = case.header
    lines = [h.name]
    meta = [h.email, h.phone, f"github.com/{h.github_login}"]
    lines.append(" | ".join(meta))
    personal = []
    if h.birth_year:
        personal.append(f"{h.birth_year}년생")
    if h.gender_cue:
        personal.append(h.gender_cue)
    if personal:
        lines.append(" / ".join(personal))
    lines.append("")

    edu = [f for f in case.profile.facts if f.category is FactCategory.EDUCATION]
    if edu:
        lines.append("## 학력")
        for f in edu:
            lines.append(f"- {h.school} {f.content}{_period(f.period)}")
        lines.append("")

    for category, title in _SECTION.items():
        facts = [f for f in case.profile.facts if f.category is category]
        if not facts:
            continue
        lines.append(f"## {title}")
        for f in facts:
            head = f"- {f.content}{_period(f.period)}"
            if f.organization and category is FactCategory.EXPERIENCE:
                head += f" — {f.organization}"
            if f.role:
                head += f" / 역할: {f.role}"
            lines.append(head)
            if f.id in case.linked_in_resume:
                lines.append(f"  - 저장소: {case.repo(case.linked_in_resume[f.id]).url}")
            for c in f.claims:
                lines.append(f"  - {c.text}")
        lines.append("")
    lines += case.resume_extra_lines
    return "\n".join(lines).rstrip() + "\n"


def render_jd(case: Case) -> str:
    """공고 원문을 만든다. 결격 요건은 필수 요건과 함께 "자격 요건"에 적는다."""
    job = case.job
    lines = [f"[{job.company}] {job.title}"]
    if job.blind:
        lines.append("※ 블라인드 채용: 지원서에 이름·학교·사진 등 개인 식별 정보를 쓰지 마십시오.")
    lines.append("")
    if job.responsibilities:
        lines.append("주요 업무")
        lines += [f"- {r}" for r in job.responsibilities]
        lines.append("")
    must = [r for r in job.requirements if r.kind is not RequirementKind.PREFERRED]
    nice = [r for r in job.requirements if r.kind is RequirementKind.PREFERRED]
    lines.append("자격 요건")
    lines += [f"- {r.text}" for r in must]
    if nice:
        lines += ["", "우대 사항"]
        lines += [f"- {r.text}" for r in nice]
    lines += case.jd_extra_lines
    return "\n".join(lines).rstrip() + "\n"
