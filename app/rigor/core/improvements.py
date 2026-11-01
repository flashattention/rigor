"""E5: 기존 저장소 개선 항목 (규칙 템플릿) + 완료 기준 평가 (재감사·NSM용).

모든 개선 항목은 재감사의 결정론 신호로 자동 확인 가능한 완료 기준을 가진다 (ADR-0007).
"""

from __future__ import annotations

from typing import Any

from rigor.core.gaps import req_cite
from rigor.core.repo_verdict import RepoJudgement
from rigor.schemas.common import Citation
from rigor.schemas.repo import (
    DoneCriterion,
    ExposureKind,
    Impact,
    Improvement,
    RepoAudit,
    RepoVerdict,
)

_SECTION_LABEL = {"problem": "문제 정의", "design": "설계", "setup": "실행 방법", "results": "결과"}


def _item(
    repo_id: int,
    key: str,
    title: str,
    desc: str,
    done: list[DoneCriterion],
    effort: float,
    impact: Impact,
    cites: list[Citation],
) -> Improvement:
    return Improvement(
        id=f"imp_{repo_id}_{key}",
        title=title,
        description=desc,
        done_criteria=done,
        effort_days=effort,
        impact=impact,
        citations=cites,
    )


def improvements_for(audit: RepoAudit, j: RepoJudgement) -> list[Improvement]:
    if j.verdict is RepoVerdict.DROP:
        return []
    rid = audit.repo_id
    cites = [req_cite(r) for r in j.relevant_requirements] or [
        Citation(kind="repo_evidence", ref_id=f"e_{rid}_summary")
    ]
    impact = Impact.HIGH if len(j.relevant_requirements) >= 2 else Impact.MED
    s = audit.signals
    out: list[Improvement] = []
    kinds = {e.kind for e in audit.exposure_findings}
    if ExposureKind.SECRET in kinds:
        out.append(
            _item(
                rid,
                "secret",
                "노출된 비밀값 제거·폐기",
                "키를 즉시 폐기하고 새로 발급한 뒤, 코드에서 제거하고 환경 변수로 옮긴다. "
                "커밋 이력에도 남아 있으므로 이력 정리가 필요하다.",
                [DoneCriterion(signal_path="exposure.secret", op="==", value=0)],
                0.5,
                Impact.HIGH,
                cites,
            )
        )
    if kinds & {ExposureKind.REAL_NAME, ExposureKind.CONTACT, ExposureKind.SCHOOL}:
        out.append(
            _item(
                rid,
                "pii",
                "README의 실명·연락처·학교명 제거",
                "블라인드 공고에서는 저장소의 식별 정보도 문제가 될 수 있다.",
                [DoneCriterion(signal_path="exposure.pii", op="==", value=0)],
                0.1,
                Impact.MED,
                cites,
            )
        )
    if not s.tests.exists or s.tests.file_count < 3:
        out.append(
            _item(
                rid,
                "tests",
                "핵심 모듈 테스트 추가",
                "핵심 로직마다 테스트를 두어 면접에서 품질 관리 습관을 보여 준다.",
                [DoneCriterion(signal_path="tests.file_count", op=">=", value=3)],
                1.0,
                impact,
                cites,
            )
        )
    if not s.ci.exists:
        out.append(
            _item(
                rid,
                "ci",
                "CI에서 테스트 자동 실행",
                "push마다 테스트가 도는 워크플로를 추가한다.",
                [DoneCriterion(signal_path="ci.exists", op="==", value=True)],
                0.5,
                impact,
                cites,
            )
        )
    missing = [k for k in ("problem", "design", "setup", "results") if k not in s.readme.sections]
    if missing:
        labels = ", ".join(_SECTION_LABEL[k] for k in missing)
        out.append(
            _item(
                rid,
                "readme",
                f"README에 {labels} 섹션 추가",
                "면접관이 1분 안에 문제·설계·실행법·결과를 파악할 수 있게 한다.",
                [
                    DoneCriterion(signal_path="readme.sections", op="contains", value=k)
                    for k in missing
                ],
                0.5 * len(missing),
                impact,
                cites,
            )
        )
    if not s.license:
        out.append(
            _item(
                rid,
                "license",
                "라이선스 파일 추가",
                "공개 저장소의 사용 조건을 밝힌다.",
                [DoneCriterion(signal_path="license", op="!=", value=None)],
                0.1,
                Impact.LOW,
                cites,
            )
        )
    return out


def _resolve(path: str, signals: dict[str, Any], exposure: dict[str, int]) -> Any:
    if path.startswith("exposure."):
        return exposure.get(path.split(".", 1)[1], 0)
    node: Any = signals
    for part in path.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def criterion_met(c: DoneCriterion, audit: RepoAudit) -> bool:
    """재감사 결과로 완료 기준 충족 여부를 판정한다 (NSM: 검증된 개선)."""
    kinds = [e.kind for e in audit.exposure_findings]
    exposure = {
        "secret": sum(k is ExposureKind.SECRET for k in kinds),
        "pii": sum(
            k in (ExposureKind.REAL_NAME, ExposureKind.CONTACT, ExposureKind.SCHOOL) for k in kinds
        ),
    }
    value = _resolve(c.signal_path, audit.signals.model_dump(), exposure)
    if c.op == "==":
        return value == c.value
    if c.op == "!=":
        return value != c.value
    if c.op == ">=":
        return value is not None and value >= c.value
    if c.op == "<=":
        return value is not None and value <= c.value
    return isinstance(value, list | str) and c.value in value
