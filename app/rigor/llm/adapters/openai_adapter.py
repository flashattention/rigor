"""OpenAI 어댑터 (ADR-0009 단계 1). SDK는 실제로 쓸 때만 import한다."""

from __future__ import annotations

from typing import Any

from rigor.llm.types import LLMProviderError, LLMRequest, TransientLLMError, Usage

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
        text = resp.choices[0].message.content or ""
        u = resp.usage
        details = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return text, Usage(
            input_tokens=u.prompt_tokens - cached,
            output_tokens=u.completion_tokens,
            cache_read_tokens=cached,
        )


def _classify(exc: Exception) -> Exception:
    if type(exc).__name__ in _TRANSIENT:
        return TransientLLMError(str(exc))
    return LLMProviderError(f"{type(exc).__name__}: {exc}")
