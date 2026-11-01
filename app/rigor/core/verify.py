"""F1·F2: 출력 검증 (M1 단순판) — 인용 무결성과 정책 검사. LLM을 쓰지 않는다.

F1: 모든 출력 항목의 인용이 실제로 존재하는 요건·fact·저장소 근거를 가리키는지. 아니면 항목을 버리고
    센다(dropped_uncited — 지표 "근거 없는 지적 비율"). claim 단위 함의 검사(LLM)는 M2.
F2: 출력 문장에 보호 속성 언급·사용자 식별 정보가 섞였는지.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from rigor.schemas.common import Citation

_PROTECTED_WORDS = re.compile(
    r"성별|여성이라|남성이라|나이가|나이 때문|연령|\d{2}\s*세라서|군필|미필|병역|장애인|기혼|미혼|"
    r"출신\s*(지역|학교)|학벌|명문대|지방대|비전공자라서|공백\s*기간\s*때문"
)


@dataclass
class EvidenceIndex:
    requirements: set[str]
    facts: set[str]
    repo_evidence: set[str]

    def valid(self, c: Citation) -> bool:
        pool = {
            "requirement": self.requirements,
            "fact": self.facts,
            "repo_evidence": self.repo_evidence,
        }
        return c.ref_id in pool[c.kind]


@dataclass
class VerifyReport:
    dropped_uncited: int = 0
    dropped_items: list[str] = field(default_factory=list)
    policy_flags: list[str] = field(default_factory=list)


def keep_cited(items: Iterable, index: EvidenceIndex, report: VerifyReport, label: str) -> list:
    """인용 중 유효한 것만 남기고, 유효한 인용이 하나도 없으면 항목을 버린다."""
    kept = []
    for item in items:
        cites = [c for c in item.citations if index.valid(c)]
        if not cites:
            report.dropped_uncited += 1
            report.dropped_items.append(
                f"{label}: {getattr(item, 'text', getattr(item, 'title', ''))[:60]}"
            )
            continue
        kept.append(item.model_copy(update={"citations": cites}))
    return kept


def policy_check(texts: Iterable[str], identifiers: Iterable[str], report: VerifyReport) -> None:
    """보호 속성 언급·식별 정보를 report에 경고로 남긴다. 문장을 고치거나 버리지는 않는다."""
    ids = [i for i in identifiers if i]
    for t in texts:
        if _PROTECTED_WORDS.search(t):
            report.policy_flags.append(f"보호 속성 언급 의심: {t[:60]}")
        for i in ids:
            if i in t:
                report.policy_flags.append("출력에 사용자 식별 정보가 포함됨")
