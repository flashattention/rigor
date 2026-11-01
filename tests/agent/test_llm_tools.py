"""tool calling 계층. 제공자 형식 변환은 가짜 SDK 클라이언트로 검증한다 (네트워크 없음)."""

import json
from types import SimpleNamespace as NS

import pytest

from rigor.llm.adapters.anthropic_adapter import AnthropicAdapter
from rigor.llm.adapters.fake import FakeAdapter
from rigor.llm.adapters.openai_adapter import OpenAIAdapter
from rigor.llm.adapters.recorded import RecordedAdapter
from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Route, Routing
from rigor.llm.types import (
    AgentMessage,
    BudgetExceeded,
    RecordingMissing,
    ToolCall,
    ToolRequest,
    ToolResult,
    ToolSpec,
    ToolTurn,
    TransientLLMError,
    Usage,
)

SPEC = ToolSpec("grep", "찾기", {"type": "object"})
HISTORY = (
    AgentMessage("user", text="주장"),
    AgentMessage("assistant", tool_calls=(ToolCall("t1", "grep", {"query": "x"}),)),
    AgentMessage("tool", results=(ToolResult("t1", "grep", "결과"),)),
)
REQ = ToolRequest("claim_verify", "m", "sys", HISTORY, (SPEC,), {"max_tokens": 100})


class Rec:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    def create(self, **kw):
        self.kwargs = kw
        return self.resp


def test_openai_converts_history_and_parses_tool_calls():
    bad = NS(id="t3", function=NS(name="grep", arguments="{not json"))
    good = NS(id="t2", function=NS(name="read_file", arguments='{"path": "a.py"}'))
    resp = NS(
        choices=[NS(finish_reason="tool_calls", message=NS(content=None, tool_calls=[good, bad]))],
        usage=NS(prompt_tokens=10, completion_tokens=2, prompt_tokens_details=None),
    )
    rec = Rec(resp)
    turn = OpenAIAdapter(client=NS(chat=NS(completions=rec))).complete_tools(
        ToolRequest("claim_verify", "m", "sys", HISTORY, (SPEC,))
    )
    roles = [m["role"] for m in rec.kwargs["messages"]]
    assert roles == ["system", "user", "assistant", "tool"]
    assert json.loads(rec.kwargs["messages"][2]["tool_calls"][0]["function"]["arguments"]) == {
        "query": "x"
    }
    assert rec.kwargs["messages"][3]["tool_call_id"] == "t1"
    assert rec.kwargs["tools"][0]["function"]["name"] == "grep"
    assert turn.tool_calls[0] == ToolCall("t2", "read_file", {"path": "a.py"})
    assert turn.tool_calls[1].invalid_arguments


def test_anthropic_converts_history_and_parses_tool_use():
    resp = NS(
        stop_reason="tool_use",
        content=[NS(type="text", text="생각"), NS(type="tool_use", id="t2", name="tree", input={})],
        usage=NS(input_tokens=10, output_tokens=2),
    )
    rec = Rec(resp)
    turn = AnthropicAdapter(client=NS(messages=rec)).complete_tools(REQ)
    msgs = rec.kwargs["messages"]
    assert msgs[1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "grep",
        "input": {"query": "x"},
    }
    assert msgs[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "결과"}],
    }
    assert rec.kwargs["max_tokens"] == 100
    assert turn.text == "생각" and turn.tool_calls == (ToolCall("t2", "tree", {}),)


def client_with(fake, max_usd=None):
    return LLMClient(
        Routing("t", Route("fake", "fake-1"), {}), {"fake": fake},
        CostMeter(Pricing.load(), max_usd=max_usd), sleep=lambda _: None,
    )  # fmt: skip


TURN = ToolTurn("", (ToolCall("1", "tree"),), Usage(input_tokens=5, output_tokens=1))


def test_client_retries_transient_errors_and_meters_each_turn():
    fake = FakeAdapter().queue_turns("claim_verify", [TransientLLMError("429"), TURN])
    client = client_with(fake)
    turn = client.tool_turn("claim_verify", system="s", messages=[], tools=[SPEC])
    assert turn.tool_calls[0].name == "tree"
    assert client.meter.calls == 1 and client.meter.usage_by_task["claim_verify"].input_tokens == 5


def test_client_stops_before_the_call_when_budget_is_gone():
    fake = FakeAdapter().queue_turns("claim_verify", [TURN])
    with pytest.raises(BudgetExceeded):
        client_with(fake, max_usd=0.0).tool_turn(
            "claim_verify", system="s", messages=[], tools=[SPEC]
        )
    assert fake.tool_requests == []


def test_tool_turns_can_be_recorded_and_replayed(tmp_path):
    fake = FakeAdapter().queue_turns("claim_verify", [TURN])
    first = RecordedAdapter("fake", "record", fake, tmp_path).complete_tools(REQ)
    replay = RecordedAdapter("fake", "replay", directory=tmp_path)
    assert replay.complete_tools(REQ).tool_calls == first.tool_calls
    changed = ToolRequest("claim_verify", "m", "sys v2", HISTORY, (SPEC,), REQ.params)
    with pytest.raises(RecordingMissing):
        replay.complete_tools(changed)
