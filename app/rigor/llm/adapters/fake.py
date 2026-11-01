"""테스트용 가짜 LLM. 네트워크 없이 task별 응답을 돌려준다."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

from pydantic import BaseModel

from rigor.llm.types import LLMRequest, ToolRequest, ToolTurn, Usage

Response = BaseModel | dict[str, Any] | str | Exception
Handler = Callable[[LLMRequest], Response]


class FakeAdapter:
    """task마다 고정 응답, 응답 큐, 또는 함수(handler)를 등록한다.

    큐에 Exception을 넣으면 그 호출에서 예외를 던진다 (재시도 테스트용).
    """

    provider = "fake"

    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._queues: dict[str, deque[Response]] = {}
        self.requests: list[LLMRequest] = []
        self._turns: dict[str, deque[ToolTurn | Exception]] = {}
        self.tool_requests: list[ToolRequest] = []

    def on(self, task: str, handler: Handler) -> FakeAdapter:
        self._handlers[task] = handler
        return self

    def queue(self, task: str, responses: Iterable[Response]) -> FakeAdapter:
        self._queues.setdefault(task, deque()).extend(responses)
        return self

    def queue_turns(self, task: str, turns: Iterable[ToolTurn | Exception]) -> FakeAdapter:
        """에이전트 턴을 순서대로 돌려준다. 큐가 비면 테스트 실패 (예상보다 많이 부름)."""
        self._turns.setdefault(task, deque()).extend(turns)
        return self

    def complete_tools(self, request: ToolRequest) -> ToolTurn:
        self.tool_requests.append(request)
        queue = self._turns.get(request.task)
        if not queue:
            raise AssertionError(f"FakeAdapter has no more tool turns for task {request.task}")
        turn = queue.popleft()
        if isinstance(turn, Exception):
            raise turn
        return turn

    def complete(self, request: LLMRequest) -> tuple[str, Usage]:
        """큐를 먼저 쓰고, 비었으면 handler를 쓴다. 둘 다 없으면 AssertionError.

        사용량은 글자 수 / 4로 근사한다.
        """
        self.requests.append(request)
        queue = self._queues.get(request.task)
        if queue:
            response = queue.popleft()
        elif request.task in self._handlers:
            response = self._handlers[request.task](request)
        else:
            raise AssertionError(f"FakeAdapter has no response for task {request.task}")
        if isinstance(response, Exception):
            raise response
        raw = _to_json(response)
        prompt = request.system + "".join(m["content"] for m in request.messages)
        return raw, Usage(
            input_tokens=max(1, len(prompt) // 4), output_tokens=max(1, len(raw) // 4)
        )


def _to_json(response: Response) -> str:
    if isinstance(response, BaseModel):
        return response.model_dump_json()
    if isinstance(response, dict):
        return json.dumps(response, ensure_ascii=False)
    return str(response)
