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


class Citation(StrictModel):
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
