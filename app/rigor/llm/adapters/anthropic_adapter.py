"""Anthropic 어댑터 (ADR-0009 단계 2, Haiku 5.5).

구조화 출력은 스키마를 도구 하나로 주고 그 도구 사용을 강제하는 방식으로 받는다.
LLM이 도구를 "고르는" 에이전트 호출이 아니다. 시스템 프롬프트(루브릭 등 고정부)는 캐시한다.
"""

from __future__ import annotations

import json
from typing import Any

from rigor.llm.types import (
    LLMProviderError,
    LLMRequest,
    ToolCall,
    ToolRequest,
    ToolTurn,
    TransientLLMError,
    TruncatedOutputError,
    Usage,
)

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

    def complete_tools(self, request: ToolRequest) -> ToolTurn:
        params = dict(request.params)
        max_tokens = params.pop("max_tokens", 4096)
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "tool":
                content = [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content}
                    for r in m.results
                ]
                messages.append({"role": "user", "content": content})
            elif m.tool_calls:
                blocks: list[dict[str, Any]] = [{"type": "text", "text": m.text}] if m.text else []
                blocks += [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                    for c in m.tool_calls
                ]
                messages.append({"role": "assistant", "content": blocks})
            else:
                messages.append({"role": m.role, "content": m.text})
        try:
            resp = self.client.messages.create(
                model=request.model,
                max_tokens=max_tokens,
                system=[
                    {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
                ],
                messages=messages,
                tools=[
                    {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                    for t in request.tools
                ],
                **params,
            )
        except Exception as exc:
            raise _classify(exc) from exc
        usage = _usage(resp.usage)
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise TruncatedOutputError(
                f"tool turn truncated at {usage.output_tokens} tokens", usage
            )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        calls = tuple(
            ToolCall(b.id, b.name, dict(b.input))
            for b in resp.content
            if getattr(b, "type", None) == "tool_use"
        )
        return ToolTurn(text=text, tool_calls=calls, usage=usage)

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
        usage = _usage(resp.usage)
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise TruncatedOutputError(
                f"output truncated at {usage.output_tokens} tokens; raise max_tokens", usage
            )
        block = next((b for b in resp.content if getattr(b, "type", None) == "tool_use"), None)
        if block is None:
            raise LLMProviderError("response had no tool_use block")
        return json.dumps(block.input, ensure_ascii=False), usage


def _usage(u: Any) -> Usage:
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _classify(exc: Exception) -> Exception:
    if type(exc).__name__ in _TRANSIENT:
        return TransientLLMError(str(exc))
    return LLMProviderError(f"{type(exc).__name__}: {exc}")
