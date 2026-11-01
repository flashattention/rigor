"""C4 1단계: claim 검증 — 규칙으로 판단할 수 있는 것만 판단한다 (ADR-0017).

가장 싼 방법부터: 규칙 → (M1b) 근거 검색 + LLM → (M2) 에이전트 탐색.
규칙으로 확신할 수 없으면 판정하지 않고 pending으로 넘긴다. 오탐(사실을 거짓으로 지적)은
사용자에게 가장 해로운 실패이므로(위험 R24), 규칙은 보수적으로 둔다.

| claim 유형 | 규칙 |
|---|---|
| 저장소 없음 | unverifiable |
| tech (+기술명 있는 feature) | 의존성·import에 있으면 supported, 저장소 어디에도 없으면 not_found |
| metric | 수치가 저장소 문서·결과 파일에 있으면 supported, 없으면 not_found |
| role | 소유·주도 표현인데 본인 커밋 비율이 낮거나, 언급한 영역에 본인 변경이 없으면 contradicted |
| period | 본인 커밋 기간과 겹치면 supported, 전혀 안 겹치면 contradicted |
| 그 외 | pending |
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from rigor.github_audit.snapshot import RepoSnapshot
from rigor.schemas.common import Citation
from rigor.schemas.profile import Claim, ClaimType, Fact
from rigor.schemas.repo import (
    CheckMethod,
    ClaimCheck,
    ClaimResult,
    ContributionAnalysis,
    Mismatch,
    RepoEvidence,
    RepoSignals,
)
from rigor.taxonomy import load_taxonomy

_OWNERSHIP = re.compile(
    r"주도|전체|리드|총괄|단독|혼자|전담|lead|led|owned|architect", re.IGNORECASE
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?\s*(?:%|분|초|시간|배|ms|s|x|건|명|만|k)?", re.IGNORECASE)
_AREA_WORDS = {
    "검색": ("retrieval", "search", "index", "rag"),
    "에이전트": ("agent",),
    "모델": ("model", "train"),
    "랭킹": ("rank", "model"),
    "피처": ("feature",),
    "서빙": ("serving", "serve", "api"),
    "평가": ("eval",),
    "문서": ("docs", "readme"),
    "프론트": ("web", "ui", "frontend"),
    "ui": ("ui", "web", "frontend"),
    "백엔드": ("api", "server", "backend"),
    "데이터": ("data", "pipeline", "dags", "etl"),
    "파이프라인": ("pipeline", "dags", "etl"),
    "인증": ("auth",),
}
LOW_SHARE = 0.25


@dataclass
class ClaimRuleResult:
    checks: list[ClaimCheck]
    pending: list[Claim]
    evidence: list[RepoEvidence]


def skills_in_text(text: str) -> set[str]:
    """claim 문장에 나온 기술명 (분류 체계의 정규 이름·별칭 기준)."""
    tax = load_taxonomy()
    found = set()
    lowered = text.lower()
    # 경계는 영숫자만 본다: "Python으로"처럼 조사가 붙어도 잡힌다
    for alias, canonical in tax.aliases().items():
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
            found.add(canonical)
    return found


def _numbers(text: str) -> list[str]:
    return [n.replace(" ", "") for n in _NUMBER.findall(text) if re.search(r"\d", n)]


class _Evidence:
    """fact 하나를 검사하며 근거를 모은다. id는 이 인스턴스 안에서만 유일하다."""

    def __init__(self, repo_id: int):
        self.repo_id = repo_id
        self.items: list[RepoEvidence] = []

    def add(
        self, path: str | None = None, line: int | None = None, quote: str | None = None
    ) -> Citation:
        ev = RepoEvidence(
            id=f"e_{self.repo_id}_{len(self.items) + 1}",
            repo_id=self.repo_id,
            path=path,
            line_start=line,
            line_end=line,
            quote=(quote or "")[:200] or None,
        )
        self.items.append(ev)
        return Citation(kind="repo_evidence", ref_id=ev.id, quote=ev.quote)


def _find(
    snap: RepoSnapshot, needle: str, paths: list[str] | None = None
) -> tuple[str, int, str] | None:
    """needle이 처음 나오는 (경로, 줄 번호, 줄 내용). 대소문자는 무시한다."""
    # 앞의 "."까지 막아 82가 0.82 안에서 일치하지 않게 한다
    pattern = re.compile(rf"(?<![A-Za-z0-9.]){re.escape(needle)}(?![A-Za-z0-9])", re.IGNORECASE)
    for path in paths or sorted(snap.files):
        text = snap.files.get(path, "")
        m = pattern.search(text)
        if m:
            line_no = text.count("\n", 0, m.start()) + 1
            return path, line_no, text.splitlines()[line_no - 1].strip()
    return None


def _overlaps(fact: Fact, period: tuple[str, str] | None) -> bool | None:
    """fact 기간과 커밋 기간이 겹치는가. 어느 쪽이든 기간을 전혀 모르면 None."""
    if not period or not (fact.period.start or fact.period.end):
        return None
    start = fact.period.start or date.min
    end = fact.period.end or date.max
    first, last = (date.fromisoformat(p) for p in period)
    return not (last < start or first > end)


_READABLE = (
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".go",
    ".java",
    ".kt",
    ".rs",
    ".ipynb",
    ".md",
    ".csv",
    ".json",
    ".txt",
)


def _read_everything(snap: RepoSnapshot) -> bool:
    """파일을 다 읽지 않았으면 "저장소에 없다"고 단정할 수 없다 (오탐 방지, 위험 R24)."""
    return all(p in snap.files for p in snap.paths if p.lower().endswith(_READABLE))


def check_claims_by_rules(
    fact: Fact,
    snap: RepoSnapshot | None,
    signals: RepoSignals | None,
    contribution: ContributionAnalysis | None,
) -> ClaimRuleResult:
    """fact의 claim을 유형별 규칙으로 판정한다 (표는 모듈 docstring). snap이 없으면 전부 unverifiable.

    판단하지 못한 claim은 checks가 아니라 pending으로 돌려준다.
    """
    if snap is None:
        checks = [
            ClaimCheck(
                claim_id=c.id,
                fact_id=fact.id,
                claim=c.text,
                result=ClaimResult.UNVERIFIABLE,
                method=CheckMethod.RULE,
                action="저장소가 없어 확인 불가 — 면접 설명 준비",
            )
            for c in fact.claims
        ]
        return ClaimRuleResult(checks, [], [])

    ev = _Evidence(snap.meta.repo_id)
    deps = set(signals.dependencies if signals else [])
    checks: list[ClaimCheck] = []
    pending: list[Claim] = []

    def done(
        c: Claim, result: ClaimResult, cites: list[Citation], action: str | None = None
    ) -> None:
        checks.append(
            ClaimCheck(
                claim_id=c.id,
                fact_id=fact.id,
                claim=c.text,
                result=result,
                method=CheckMethod.RULE,
                evidence=cites,
                action=action,
            )
        )

    for c in fact.claims:
        if c.type in (ClaimType.TECH, ClaimType.FEATURE):
            skills = skills_in_text(c.text)
            if not skills:
                pending.append(c)
                continue
            hit = sorted(skills & deps)
            if (
                hit and skills <= deps
            ):  # 나열된 기술이 모두 있어야 확인됨. 일부만 있으면 아래에서 보류
                where = _find(snap, hit[0]) or _find(snap, hit[0].replace("_", "-"))
                cite = ev.add(*(where or (None, None, f"의존성: {hit[0]}")))
                done(c, ClaimResult.SUPPORTED, [cite])
            elif _read_everything(snap) and not any(_find(snap, s) for s in skills):
                done(
                    c,
                    ClaimResult.NOT_FOUND,
                    [],
                    f"이력서 수정 필요: {', '.join(sorted(skills))} 사용 근거가 저장소에 없음",
                )
            else:
                pending.append(c)  # 언급은 있지만 의존성은 아님 → 사람이 읽어야 판단 가능
        elif c.type is ClaimType.METRIC:
            nums = _numbers(c.text)
            docs = [
                p
                for p in snap.files
                if p.lower().endswith((".md", ".csv", ".json", ".txt", ".rst"))
            ]
            where = next((w for n in nums if (w := _find(snap, n, docs))), None)
            if where:
                done(c, ClaimResult.SUPPORTED, [ev.add(*where)])
            elif nums and _read_everything(snap):
                done(
                    c,
                    ClaimResult.NOT_FOUND,
                    [],
                    "수치의 측정 근거(결과 표·스크립트)를 저장소에 추가하거나 수치를 고칠 것",
                )
            else:
                pending.append(c)
        elif c.type is ClaimType.ROLE and contribution is not None:
            share = contribution.user_share
            stat = ev.add(
                quote=f"본인 커밋 {contribution.user_commits}/{contribution.total_commits}"
            )
            areas = [k for k in _AREA_WORDS if k in c.text.lower()]
            area_user = area_total = 0
            for a in contribution.areas:
                if any(key in a.path_prefix.lower() for k in areas for key in _AREA_WORDS[k]):
                    area_user += a.user_changes
                    area_total += a.total_changes
            # API 스냅샷은 변경 통계가 없어 areas가 비므로 영역 규칙은 적용되지 않는다
            if share >= 0.999:
                done(c, ClaimResult.SUPPORTED, [stat])
            elif _OWNERSHIP.search(c.text) and share < LOW_SHARE:
                done(
                    c,
                    ClaimResult.CONTRADICTED,
                    [stat],
                    "기여 범위를 커밋 근거에 맞게 고치거나 근거를 보충할 것",
                )
            elif areas and area_total and area_user == 0:
                done(c, ClaimResult.CONTRADICTED, [stat], "언급한 영역에 본인 변경 기록이 없음")
            elif areas and area_total and area_user / area_total >= 0.5:
                done(c, ClaimResult.SUPPORTED, [stat])
            else:
                pending.append(c)
        elif c.type is ClaimType.PERIOD:
            overlap = _overlaps(fact, contribution.active_period if contribution else None)
            if overlap is None:
                pending.append(c)
            else:
                cite = ev.add(quote=f"본인 커밋 기간 {contribution.active_period}")
                done(c, ClaimResult.SUPPORTED if overlap else ClaimResult.CONTRADICTED, [cite])
        else:
            pending.append(c)
    return ClaimRuleResult(checks, pending, ev.items)


MISMATCH_FOR = {
    (ClaimType.TECH, ClaimResult.NOT_FOUND): Mismatch.MISSING_IN_CODE,
    (ClaimType.FEATURE, ClaimResult.NOT_FOUND): Mismatch.MISSING_IN_CODE,
    (ClaimType.METRIC, ClaimResult.NOT_FOUND): Mismatch.METRIC_UNSUPPORTED,
    (ClaimType.ROLE, ClaimResult.CONTRADICTED): Mismatch.CONTRIBUTION_OVERSTATED,
}
