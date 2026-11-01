"""모든 출력이 공유하는 근거(Citation)와 재현 메타데이터(Provenance).

docs/schemas.md §1. 진단·추천 항목은 Citation을 최소 1개 가져야 한다 — 근거 없는 말은 하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """알 수 없는 필드를 거부한다. LLM이 스키마 밖의 값을 끼워 넣는 것을 막는다."""

    model_config = ConfigDict(extra="forbid")


class DraftModel(BaseModel):
    """LLM 출력용 초안. 스키마에 없는 필드는 **버린다** (거부하지 않는다).

    모델이 필드를 덧붙여도(실제 gpt-5-nano 실행에서 관찰) 그 값은 어디에도 쓰이지 않으므로
    "스키마 밖 값이 들어오는 것"은 막으면서 불필요한 실패를 줄인다. 도메인 스키마는 StrictModel.
    """

    model_config = ConfigDict(extra="ignore")


class Citation(StrictModel):
    """근거 한 건. ref_id는 kind에 따라 Requirement·Fact·RepoEvidence의 id를 가리킨다."""

    kind: Literal["requirement", "fact", "repo_evidence"]
    ref_id: str = Field(min_length=1)
    quote: str | None = None


Citations = Annotated[list[Citation], Field(min_length=1)]


class Provenance(StrictModel):
    provider: str
    model_id: str
    routing_hash: str
    prompt_version: str
    rubric_version: str | None = None
    taxonomy_version: str | None = None
    analyzer_version: str | None = None
    trace_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
