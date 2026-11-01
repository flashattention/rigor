"""제공자 어댑터는 가짜 SDK 클라이언트로 요청 형태와 사용량 해석만 검증한다 (네트워크 없음)."""

import json
from types import SimpleNamespace

import pytest

from rigor.llm.adapters.anthropic_adapter import AnthropicAdapter
from rigor.llm.adapters.fake import FakeAdapter
from rigor.llm.adapters.openai_adapter import OpenAIAdapter
from rigor.llm.adapters.recorded import RecordedAdapter, recording_key
from rigor.llm.types import LLMProviderError, LLMRequest, RecordingMissing, TransientLLMError

REQ = LLMRequest(
    task="jd_parse",
    model="m",
    system="sys",
    messages=({"role": "user", "content": "hi"},),
    schema_name="Answer",
    json_schema={"type": "object"},
    params={"max_tokens": 100},
)


class RateLimitError(Exception):
    pass


class Recorder:
    def __init__(self, response=None, error=None):
        self.kwargs = None
        self.response = response
        self.error = error

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def test_openai_adapter_request_and_cached_usage():
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"a":1}'))],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
        ),
    )
    rec = Recorder(resp)
    adapter = OpenAIAdapter(client=SimpleNamespace(chat=SimpleNamespace(completions=rec)))
    raw, usage = adapter.complete(REQ)
    assert raw == '{"a":1}'
    assert (usage.input_tokens, usage.cache_read_tokens, usage.output_tokens) == (60, 40, 20)
    assert rec.kwargs["messages"][0] == {"role": "system", "content": "sys"}
    assert rec.kwargs["response_format"]["json_schema"]["name"] == "Answer"


def test_openai_rate_limit_is_transient():
    rec = Recorder(error=RateLimitError("slow down"))
    adapter = OpenAIAdapter(client=SimpleNamespace(chat=SimpleNamespace(completions=rec)))
    with pytest.raises(TransientLLMError):
        adapter.complete(REQ)


def test_anthropic_adapter_forces_schema_tool_and_caches_system():
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input={"a": 1})],
        usage=SimpleNamespace(
            input_tokens=50,
            output_tokens=10,
            cache_read_input_tokens=30,
            cache_creation_input_tokens=5,
        ),
    )
    rec = Recorder(resp)
    adapter = AnthropicAdapter(client=SimpleNamespace(messages=rec))
    raw, usage = adapter.complete(REQ)
    assert json.loads(raw) == {"a": 1}
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (30, 5)
    assert rec.kwargs["tool_choice"] == {"type": "tool", "name": "Answer"}
    assert rec.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert rec.kwargs["max_tokens"] == 100


def test_anthropic_without_tool_use_is_provider_error():
    resp = SimpleNamespace(content=[SimpleNamespace(type="text")], usage=None)
    adapter = AnthropicAdapter(client=SimpleNamespace(messages=Recorder(resp)))
    with pytest.raises(LLMProviderError):
        adapter.complete(REQ)


def test_record_then_replay_round_trip(tmp_path):
    inner = FakeAdapter().queue("jd_parse", [{"a": 1}])
    recorder = RecordedAdapter("fake", "record", inner, tmp_path)
    raw, usage = recorder.complete(REQ)
    replay = RecordedAdapter("fake", "replay", directory=tmp_path)
    assert replay.complete(REQ) == (raw, usage)


def test_replay_misses_when_prompt_changes(tmp_path):
    inner = FakeAdapter().queue("jd_parse", [{"a": 1}])
    RecordedAdapter("fake", "record", inner, tmp_path).complete(REQ)
    changed = LLMRequest(**{**REQ.__dict__, "system": "sys v2"})
    assert recording_key(changed) != recording_key(REQ)
    with pytest.raises(RecordingMissing):
        RecordedAdapter("fake", "replay", directory=tmp_path).complete(changed)


def test_openai_length_finish_raises_truncated_with_usage():
    from rigor.llm.types import TruncatedOutputError

    resp = SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content=""))],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=8000,
            prompt_tokens_details=None,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=8000),
        ),
    )
    adapter = OpenAIAdapter(
        client=SimpleNamespace(chat=SimpleNamespace(completions=Recorder(resp)))
    )
    with pytest.raises(TruncatedOutputError) as exc:
        adapter.complete(REQ)
    assert exc.value.usage.output_tokens == 8000
