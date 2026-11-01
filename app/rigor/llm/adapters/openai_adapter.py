"""OpenAI 어댑터 (ADR-0009 단계 1). SDK는 실제로 쓸 때만 import한다."""

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
}


class OpenAIAdapter:
    provider = "openai"

    def __init__(self, client: Any | None = None, timeout_s: float = 60.0):
        if client is None:
            from openai import OpenAI

            # 재시도는 LLMClient가 한 곳에서 한다.
            client = OpenAI(timeout=timeout_s, max_retries=0)
        self.client = client

    def complete_tools(self, request: ToolRequest) -> ToolTurn:
        """도구 턴 한 번. 인자가 깨진 호출은 예외 없이 invalid_arguments=True로 돌려준다."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": request.system}]
        for m in request.messages:
            # OpenAI는 도구 결과를 호출마다 별도 메시지로 받는다.
            if m.role == "tool":
                messages += [
                    {"role": "tool", "tool_call_id": r.call_id, "content": r.content}
                    for r in m.results
                ]
            elif m.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": m.text or None,
                        "tool_calls": [
                            {
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": json.dumps(c.arguments, ensure_ascii=False),
                                },
                            }
                            for c in m.tool_calls
                        ],
                    }
                )
            else:
                messages.append({"role": m.role, "content": m.text})
        try:
            resp = self.client.chat.completions.create(
                model=request.model,
                messages=messages,
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        },
                    }
                    for t in request.tools
                ],
                **request.params,
            )
        except Exception as exc:
            raise _classify(exc) from exc
        choice = resp.choices[0]
        usage = _usage(resp.usage)
        if getattr(choice, "finish_reason", None) == "length":
            raise TruncatedOutputError(
                f"tool turn truncated at {usage.output_tokens} tokens", usage
            )
        calls = []
        for c in choice.message.tool_calls or []:
            try:
                calls.append(
                    ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
                )
            except json.JSONDecodeError:
                calls.append(ToolCall(c.id, c.function.name, invalid_arguments=True))
        return ToolTurn(text=choice.message.content or "", tool_calls=tuple(calls), usage=usage)

    def complete(self, request: LLMRequest) -> tuple[str, Usage]:
        try:
            resp = self.client.chat.completions.create(
                model=request.model,
                messages=[{"role": "system", "content": request.system}, *request.messages],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "schema": request.json_schema,
                        "strict": False,  # 검증은 Pydantic이 한다
                    },
                },
                **request.params,
            )
        except Exception as exc:
            raise _classify(exc) from exc
        choice = resp.choices[0]
        text = choice.message.content or ""
        u = resp.usage
        usage = _usage(u)
        if getattr(choice, "finish_reason", None) == "length":
            reasoning = getattr(
                getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0
            )
            raise TruncatedOutputError(
                f"output truncated at {u.completion_tokens} tokens (reasoning {reasoning}); "
                "raise max_completion_tokens or lower reasoning_effort in routing.yaml",
                usage,
            )
        return text, usage


def _usage(u: Any) -> Usage:
    """OpenAI의 prompt_tokens는 캐시분을 포함하므로 빼서 Usage 정의에 맞춘다."""
    details = getattr(u, "prompt_tokens_details", None)
    cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
    return Usage(
        input_tokens=u.prompt_tokens - cached,
        output_tokens=u.completion_tokens,
        cache_read_tokens=cached,
    )


def _classify(exc: Exception) -> Exception:
    """클래스 이름으로 분류한다. 이 모듈은 SDK를 모듈 수준에서 import하지 않는다."""
    if type(exc).__name__ in _TRANSIENT:
        return TransientLLMError(str(exc))
    return LLMProviderError(f"{type(exc).__name__}: {exc}")
