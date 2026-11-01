"""노드가 쓰는 유일한 LLM 진입점.

- task로 모델을 해석한다 (routing).
- 일시 오류는 지수 백오프로 재시도한다.
- 출력이 스키마를 통과하지 못하면 오류를 붙여 1회 재요청한다.
- 모든 호출의 비용을 누적하고, 예산을 넘으면 호출 전에 멈춘다.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

from pydantic import BaseModel, ValidationError

from rigor.llm.budget import CostMeter
from rigor.llm.routing import Routing
from rigor.llm.types import (
    AgentMessage,
    LLMAdapter,
    LLMRequest,
    LLMResult,
    StructuredOutputError,
    ToolRequest,
    ToolSpec,
    ToolTurn,
    TransientLLMError,
    TruncatedOutputError,
    Usage,
)


class LLMClient:
    def __init__(
        self,
        routing: Routing,
        adapters: Mapping[str, LLMAdapter],
        meter: CostMeter,
        *,
        max_transient_retries: int = 3,
        max_validation_retries: int = 1,
        base_delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.routing = routing
        self.adapters = dict(adapters)
        self.meter = meter
        self.max_transient_retries = max_transient_retries
        self.max_validation_retries = max_validation_retries
        self.base_delay_s = base_delay_s
        self._sleep = sleep

    def structured[T: BaseModel](
        self,
        task: str,
        *,
        system: str,
        messages: Sequence[Mapping[str, str]],
        schema: type[T],
    ) -> LLMResult[T]:
        route = self.routing.resolve(task)
        try:
            adapter = self.adapters[route.provider]
        except KeyError as exc:
            raise KeyError(f"no adapter registered for provider {route.provider}") from exc

        msgs = tuple(dict(m) for m in messages)
        total = Usage()
        total_cost = 0.0
        attempts = 0
        for validation_try in range(self.max_validation_retries + 1):
            request = LLMRequest(
                task=task,
                model=route.model,
                system=system,
                messages=msgs,
                schema_name=schema.__name__,
                json_schema=schema.model_json_schema(),
                params=dict(route.params),
            )
            try:
                raw, usage, n = self._complete_with_retry(adapter, request)
            except TruncatedOutputError as err:
                # 비용은 이미 발생했다 → 계측하고, 같은 설정으로 재요청하지 않는다.
                self.meter.add(task, route.provider, route.model, err.usage)
                raise
            attempts += n
            total = total + usage
            total_cost += self.meter.add(task, route.provider, route.model, usage)
            try:
                value = schema.model_validate_json(raw)
            except ValidationError as err:
                if validation_try == self.max_validation_retries:
                    raise StructuredOutputError(
                        f"{task}: output failed {schema.__name__} validation: {err}"
                    ) from err
                msgs = (*msgs, *_repair_turn(raw, err))
                continue
            return LLMResult(
                value=value,
                usage=total,
                provider=route.provider,
                model=route.model,
                routing_hash=self.routing.hash,
                cost_usd=total_cost,
                attempts=attempts,
            )
        raise AssertionError("unreachable")

    def tool_turn(
        self,
        task: str,
        *,
        system: str,
        messages: Sequence[AgentMessage],
        tools: Sequence[ToolSpec],
    ) -> ToolTurn:
        """에이전트의 한 턴. 재시도·비용 계측·예산 차단은 structured와 같다."""
        route = self.routing.resolve(task)
        try:
            adapter = self.adapters[route.provider]
        except KeyError as exc:
            raise KeyError(f"no adapter registered for provider {route.provider}") from exc
        request = ToolRequest(
            task=task,
            model=route.model,
            system=system,
            messages=tuple(messages),
            tools=tuple(tools),
            params=dict(route.params),
        )
        try:
            turn = self._retry(lambda: adapter.complete_tools(request))
        except TruncatedOutputError as err:
            self.meter.add(task, route.provider, route.model, err.usage)
            raise
        cost = self.meter.add(task, route.provider, route.model, turn.usage)
        return replace(turn, cost_usd=cost)

    def _complete_with_retry(
        self, adapter: LLMAdapter, request: LLMRequest
    ) -> tuple[str, Usage, int]:
        attempts = 0

        def call() -> tuple[str, Usage]:
            nonlocal attempts
            attempts += 1
            return adapter.complete(request)

        raw, usage = self._retry(call)
        return raw, usage, attempts

    def _retry[R](self, call: Callable[[], R]) -> R:
        for attempt in range(self.max_transient_retries + 1):
            self.meter.check()
            try:
                return call()
            except TransientLLMError:
                if attempt == self.max_transient_retries:
                    raise
                delay = self.base_delay_s * (2**attempt)
                self._sleep(delay + random.uniform(0, delay / 2))
        raise AssertionError("unreachable")


def _repair_turn(raw: str, err: ValidationError) -> tuple[dict[str, str], ...]:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in err.errors()[:10]
    )
    return (
        {"role": "assistant", "content": raw},
        {
            "role": "user",
            "content": (
                "직전 출력이 스키마 검증에 실패했다. 문제: "
                f"{problems}. 스키마에 맞는 JSON 하나만 다시 출력하라."
            ),
        },
    )
