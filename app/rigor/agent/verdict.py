"""에이전트 판정을 코드로 검증한다 (ADR-0020 결정 4). 에이전트의 말을 그대로 믿지 않는다.

- supported·contradicted: 인용문이 에이전트가 실제로 연 파일의 그 줄 근처에 있어야 한다.
- not_found: 읽을 수 있는 파일을 전부 살폈을 때만 인정한다.
- 그 밖에는 unverifiable로 낮춘다. 저장소의 인젝션이 판정을 만들어 내지 못하는 이유가 이것이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, ValidationError

from rigor.agent.tools import RepoWorkspace
from rigor.schemas.common import Citation, DraftModel
from rigor.schemas.profile import Claim
from rigor.schemas.repo import CheckMethod, ClaimCheck, ClaimResult, RepoEvidence

LINE_TOLERANCE = 1  # 줄 번호가 한 줄 어긋나는 것은 허용한다


class CitationDraft(DraftModel):
    path: str
    line: int = Field(ge=1)
    quote: str = Field(min_length=1)


class VerdictSubmission(DraftModel):
    result: ClaimResult
    reason: str = ""
    citations: list[CitationDraft] = Field(default_factory=list)


@dataclass
class CheckedVerdict:
    """코드 검증을 마친 판정. downgraded는 제출한 판정이 unverifiable로 낮춰졌다는 뜻이다."""

    result: ClaimResult
    reason: str
    verified: list[CitationDraft] = field(default_factory=list)
    rejected: list[CitationDraft] = field(default_factory=list)
    downgraded: bool = False


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def citation_holds(ws: RepoWorkspace, c: CitationDraft) -> bool:
    """연 파일의 해당 줄 ±LINE_TOLERANCE 안에 인용문이 있는지 본다. 공백·대소문자는 무시한다."""
    if c.path not in ws.opened:
        return False
    text = ws.file_text(c.path)
    if text is None:
        return False
    lines = text.splitlines()
    low = max(0, c.line - 1 - LINE_TOLERANCE)
    window = _squash(" ".join(lines[low : c.line + LINE_TOLERANCE]))
    return bool(window) and _squash(c.quote) in window


def check_submission(ws: RepoWorkspace, arguments: dict[str, Any]) -> CheckedVerdict:
    """submit_verdict의 인자를 검증한다. 형식이 틀리면 unverifiable."""
    try:
        sub = VerdictSubmission.model_validate(arguments)
    except ValidationError:
        return CheckedVerdict(ClaimResult.UNVERIFIABLE, "제출 형식 오류", downgraded=True)
    verified = [c for c in sub.citations if citation_holds(ws, c)]
    rejected = [c for c in sub.citations if not citation_holds(ws, c)]
    result, reason, downgraded = sub.result, sub.reason, False
    if result in (ClaimResult.SUPPORTED, ClaimResult.CONTRADICTED) and not verified:
        result, reason, downgraded = ClaimResult.UNVERIFIABLE, "인용을 파일에서 확인하지 못함", True
    elif result is ClaimResult.NOT_FOUND and not ws.searched_everything:
        result, reason, downgraded = ClaimResult.UNVERIFIABLE, "저장소를 다 살피지 못함", True
    return CheckedVerdict(result, reason, verified, rejected, downgraded)


def to_claim_check(
    claim: Claim, checked: CheckedVerdict, repo_id: int
) -> tuple[ClaimCheck, list[RepoEvidence]]:
    """검증된 인용만 RepoEvidence로 옮겨 ClaimCheck를 만든다. rejected 인용은 남기지 않는다."""
    evidence = [
        RepoEvidence(
            id=f"e_{repo_id}_{claim.id}_{i}",
            repo_id=repo_id,
            path=c.path,
            line_start=c.line,
            line_end=c.line,
            quote=c.quote[:200],
        )
        for i, c in enumerate(checked.verified, start=1)
    ]
    check = ClaimCheck(
        claim_id=claim.id,
        fact_id=claim.fact_id,
        claim=claim.text,
        result=checked.result,
        method=CheckMethod.AGENT,
        evidence=[Citation(kind="repo_evidence", ref_id=e.id, quote=e.quote) for e in evidence],
        action=checked.reason or None,
    )
    return check, evidence
