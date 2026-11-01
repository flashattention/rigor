"""E4: 저장소 품질 점수와 공고 기준 판정 (rubric.md §4). 규칙만 쓴다.

판정은 (저장소, 공고) 쌍 기준이다. 같은 저장소가 공고 A에는 대표, 공고 B에는 제외일 수 있다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from rigor.github_audit.snapshot import RepoSnapshot
from rigor.schemas.job import JobPosting, RequirementKind
from rigor.schemas.repo import ExposureKind, RepoAudit, RepoVerdict
from rigor.taxonomy import load_taxonomy

SHOWCASE_MIN_QUALITY = 0.65
DROP_BELOW_QUALITY = 0.3
MAX_SCORE = 21  # Q1~Q10 각 2점 + Q11 1점
_RESULT_FILES = re.compile(r"(^|/)(eval|evals|results?|benchmarks?)(/|\.|$)|benchmark", re.I)
_TUTORIAL = re.compile(
    r"튜토리얼|따라\s*(하기|했다|한)|tutorial|follow(ed|ing)? along|course", re.I
)
_SOURCE_EXT = (".py", ".ts", ".tsx", ".js", ".go", ".java", ".kt", ".rs")


@dataclass
class RepoJudgement:
    repo_id: int
    scores: dict[str, float]
    quality: float
    relevant_requirements: list[str]
    verdict: RepoVerdict
    blockers: list[str] = field(default_factory=list)  # 대표가 될 수 없는 이유
    notes: list[str] = field(default_factory=list)

    @property
    def order_key(self) -> tuple[int, float]:
        rank = {RepoVerdict.SHOWCASE: 0, RepoVerdict.IMPROVE: 1, RepoVerdict.DROP: 2}[self.verdict]
        return rank, -(len(self.relevant_requirements) * self.quality)


def quality_scores(audit: RepoAudit, snap: RepoSnapshot) -> dict[str, float]:
    s = audit.signals
    sec = set(s.readme.sections)
    has_results_files = any(_RESULT_FILES.search(p) for p in snap.paths)
    source = [p for p in snap.paths if p.endswith(_SOURCE_EXT) and not p.startswith("tests")]
    dirs = {PurePosixPath(p).parts[0] for p in source if "/" in p}
    ci_runs_tests = any(
        re.search(r"pytest|npm test|go test|unittest|vitest|jest", snap.files.get(p, ""))
        for p in snap.paths
        if p.startswith(".github/workflows/")
    )
    return {
        "Q1": 2.0 if "problem" in sec else 0.0,
        "Q2": 2.0 if "design" in sec else 0.0,
        "Q3": 2.0 if "setup" in sec else 0.0,
        "Q4": 2.0 if "results" in sec else 0.0,
        "Q5": 0.0 if not s.tests.exists else (2.0 if s.tests.file_count >= 3 else 1.0),
        "Q6": 0.0 if not s.ci.exists else (2.0 if ci_runs_tests else 1.0),
        "Q7": 2.0 if (s.deploy.config_files or s.deploy.homepage_url) else 0.0,
        "Q8": 2.0 if len(dirs) >= 2 else (1.0 if dirs else 0.0),
        "Q9": 2.0 if s.commits.active_weeks >= 3 else (1.0 if s.commits.active_weeks == 2 else 0.0),
        "Q10": 2.0 if has_results_files else (1.0 if "results" in sec else 0.0),
        "Q11": 1.0 if s.license else 0.0,
    }


def relevant_requirements(
    audit: RepoAudit, job: JobPosting, cited_requirements: set[str]
) -> list[str]:
    """공고의 필수·결격 요건 중 이 저장소와 관련된 것.

    결정론(의존성의 역량·기술이 요건과 겹침) ∪ 요건 판정에서 이 저장소의 근거가 인용됨.
    """
    tax = load_taxonomy()
    deps = set(audit.signals.dependencies)
    comps = {c for d in deps for c in tax.skill_competencies.get(d, ())}
    out = []
    for r in job.requirements:
        if r.kind is RequirementKind.PREFERRED:
            continue
        if r.id in cited_requirements or (set(r.competencies) & comps) or (set(r.skills) & deps):
            out.append(r.id)
    return out


def judge_repo(
    audit: RepoAudit, snap: RepoSnapshot, job: JobPosting, cited_requirements: set[str]
) -> RepoJudgement:
    scores = quality_scores(audit, snap)
    quality = round(sum(scores.values()) / MAX_SCORE, 3)
    relevant = relevant_requirements(audit, job, cited_requirements)
    blockers, notes = [], []
    kinds = {e.kind for e in audit.exposure_findings}
    if ExposureKind.SECRET in kinds:
        blockers.append("X1 비밀값 노출")
    if job.blind and kinds & {ExposureKind.REAL_NAME, ExposureKind.CONTACT, ExposureKind.SCHOOL}:
        blockers.append("X2 블라인드 공고인데 실명·연락처·학교 노출")
    readme = next((t for p, t in snap.files.items() if p.lower().startswith("readme")), "")
    contribution = audit.contribution
    if contribution and contribution.total_commits and contribution.user_commits == 0:
        return RepoJudgement(
            audit.repo_id,
            scores,
            quality,
            relevant,
            RepoVerdict.DROP,
            notes=["X3 본인 커밋 없음 (포크 또는 복사본)"],
        )
    if _TUTORIAL.search(readme) and audit.signals.commits.total <= 5:
        blockers.append("X4 튜토리얼을 따라 한 저장소로 보임 (휴리스틱)")
    if not relevant:
        if quality >= DROP_BELOW_QUALITY:
            notes.append("이 공고와는 관련이 없지만 다른 직무에는 쓸 수 있음")
        verdict = RepoVerdict.DROP
    elif quality >= SHOWCASE_MIN_QUALITY and not blockers:
        verdict = RepoVerdict.SHOWCASE
    else:
        verdict = RepoVerdict.IMPROVE
    return RepoJudgement(audit.repo_id, scores, quality, relevant, verdict, blockers, notes)
