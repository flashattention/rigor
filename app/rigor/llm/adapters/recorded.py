"""녹화·재생 어댑터. CI의 회귀 게이트는 녹화를 재생하므로 0원이다.

녹화 키 = (task, model, system, messages, 스키마, params)의 해시. 프롬프트·입력·라우팅이
바뀌면 키가 달라져 RecordingMissing이 난다 → 다시 녹화해야 한다는 사실이 드러난다.
녹화에는 합성 데이터만 들어가야 한다 (실제 개인 이력은 저장소에 넣지 않는다).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from rigor.llm.types import LLMAdapter, LLMRequest, RecordingMissing, Usage

DEFAULT_DIR = Path(__file__).resolve().parents[4] / "evals" / "recordings"


def recording_key(request: LLMRequest) -> str:
    blob = json.dumps(
        {
            "task": request.task,
            "model": request.model,
            "system": request.system,
            "messages": list(request.messages),
            "schema_name": request.schema_name,
            "json_schema": request.json_schema,
            "params": request.params,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:24]


class RecordedAdapter:
    def __init__(
        self,
        provider: str,
        mode: Literal["replay", "record"],
        inner: LLMAdapter | None = None,
        directory: Path = DEFAULT_DIR,
    ):
        if mode == "record" and inner is None:
            raise ValueError("record mode needs an inner adapter")
        self.provider = provider
        self.mode = mode
        self.inner = inner
        self.directory = directory

    def _path(self, request: LLMRequest) -> Path:
        return self.directory / request.task / f"{recording_key(request)}.json"

    def complete(self, request: LLMRequest) -> tuple[str, Usage]:
        path = self._path(request)
        if self.mode == "replay":
            if not path.exists():
                raise RecordingMissing(
                    f"no recording for task={request.task} key={path.stem}; re-record with "
                    "a live adapter (inputs, prompt, or routing changed)"
                )
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["raw"], Usage(**data["usage"])
        assert self.inner is not None
        raw, usage = self.inner.complete(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"model": request.model, "raw": raw, "usage": usage.model_dump()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return raw, usage
