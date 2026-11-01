"""LLM 계층의 공통 타입과 오류."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel


class Usage(BaseModel):
    """토큰 사용량. input_tokens는 캐시되지 않은 입력만 센다."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True)
class LLMRequest:
    """어댑터에 전달되는 요청. 제공자 차이는 어댑터가 흡수한다."""

    task: str
    model: str
    system: str
    messages: tuple[dict[str, str], ...]
    schema_name: str
    json_schema: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    value: T
    usage: Usage
    provider: str
    model: str
    routing_hash: str
    cost_usd: float
    attempts: int


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """모델이 고른 도구 호출. arguments가 올바른 JSON이 아니면 invalid_arguments=True."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    invalid_arguments: bool = False


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    content: str


@dataclass(frozen=True)
class AgentMessage:
    """제공자와 무관한 대화 기록. 형식 변환은 어댑터가 한다."""

    role: Literal["user", "assistant", "tool"]
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    results: tuple[ToolResult, ...] = ()


@dataclass(frozen=True)
class ToolRequest:
    task: str
    model: str
    system: str
    messages: tuple[AgentMessage, ...]
    tools: tuple[ToolSpec, ...]
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolTurn:
    """모델의 한 턴: 도구를 부르거나, 도구 없이 글로 답한다."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    cost_usd: float = 0.0


class LLMAdapter(Protocol):
    provider: str

    def complete_tools(self, request: ToolRequest) -> ToolTurn:
        """도구 호출이 있는 한 턴. 다음 행동은 호출한 쪽(에이전트 루프)이 정한다."""
        ...

    def complete(self, request: LLMRequest) -> tuple[str, Usage]:
        """JSON 문자열과 사용량을 돌려준다. 일시 오류는 TransientLLMError로 올린다."""
        ...


class LLMError(Exception):
    pass


class TransientLLMError(LLMError):
    """재시도할 가치가 있는 오류 (요청 한도, 타임아웃, 5xx, 연결)."""


class LLMProviderError(LLMError):
    """재시도해도 소용없는 오류 (인증, 잘못된 요청 등)."""


class TruncatedOutputError(LLMProviderError):
    """출력 토큰 한도에 걸려 본문이 잘림/비어 있음. 같은 설정으로 재요청해도 소용없다.

    비용은 이미 발생했으므로 사용량을 함께 싣는다 (클라이언트가 계측한다).
    """

    def __init__(self, message: str, usage: Usage):
        super().__init__(message)
        self.usage = usage


class StructuredOutputError(LLMError):
    """재요청 후에도 출력이 스키마 검증을 통과하지 못함."""


class BudgetExceeded(LLMError):
    pass


class RecordingMissing(LLMError):
    """재생 모드에서 녹화가 없음. 입력·프롬프트·라우팅이 바뀌었다는 뜻이다."""
