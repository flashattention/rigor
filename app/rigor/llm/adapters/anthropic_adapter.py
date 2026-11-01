"""Anthropic 어댑터 (ADR-0009 단계 2, Haiku 5.5).

구조화 출력은 스키마를 도구 하나로 주고 그 도구 사용을 강제하는 방식으로 받는다.
LLM이 도구를 "고르는" 에이전트 호출이 아니다. 시스템 프롬프트(루브릭 등 고정부)는 캐시한다.
"""

from __future__ import annotations

import json
from typing import Any

from rigor.llm.types import LLMProviderError, LLMRequest, TransientLLMError, Usage

_TRANSIENT = {
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
    "InternalServerError",
    "OverloadedError",
}


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, client: Any | None = None, timeout_s: float = 60.0):
        if client is None:
            from anthropic import Anthropic

            client = Anthropic(timeout=timeout_s, max_retries=0)
        self.client = client

    def complete(self, request: LLMRequest) -> tuple[str, Usage]:
        params = dict(request.params)
        max_tokens = params.pop("max_tokens", 4096)
        try:
            resp = self.client.messages.create(
                model=request.model,
                max_tokens=max_tokens,
                system=[
                    {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=list(request.messages),
                tools=[
                    {
                        "name": request.schema_name,
                        "description": f"{request.schema_name} 스키마에 맞는 결과를 제출한다.",
                        "input_schema": request.json_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": request.schema_name},
                **params,
            )
        except Exception as exc:
            raise _classify(exc) from exc
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise LLMProviderError("response had no tool_use block")
        u = resp.usage
        return json.dumps(block.input, ensure_ascii=False), Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )


def _classify(exc: Exception) -> Exception:
    if type(exc).__name__ in _TRANSIENT:
        return TransientLLMError(str(exc))
    return LLMProviderError(f"{type(exc).__name__}: {exc}")
