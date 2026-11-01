import pytest
from pydantic import BaseModel

from rigor.llm.adapters.fake import FakeAdapter
from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Route, Routing
from rigor.llm.types import (
    BudgetExceeded,
    LLMProviderError,
    StructuredOutputError,
    TransientLLMError,
    Usage,
)


class Answer(BaseModel):
    label: str
    score: int


def make_client(fake: FakeAdapter, max_usd: float | None = None, retries: int = 3):
    routing = Routing("test", Route("fake", "fake-1"), {})
    meter = CostMeter(Pricing.load(), max_usd=max_usd)
    return LLMClient(
        routing, {"fake": fake}, meter, max_transient_retries=retries, sleep=lambda _: None
    )


def call(client):
    return client.structured(
        "jd_parse", system="s", messages=[{"role": "user", "content": "u"}], schema=Answer
    )


def test_returns_validated_value_with_provenance():
    fake = FakeAdapter().queue("jd_parse", [{"label": "ok", "score": 3}])
    result = call(make_client(fake))
    assert result.value == Answer(label="ok", score=3)
    assert (result.provider, result.model, result.attempts) == ("fake", "fake-1", 1)
    assert len(result.routing_hash) == 12


def test_invalid_output_is_repaired_once_with_error_feedback():
    fake = FakeAdapter().queue("jd_parse", [{"label": "ok"}, {"label": "ok", "score": 1}])
    result = call(make_client(fake))
    assert result.value.score == 1
    repair = fake.requests[1].messages[-1]["content"]
    assert "score" in repair and "스키마" in repair


def test_invalid_output_twice_raises():
    fake = FakeAdapter().queue("jd_parse", ["not json", "still not json"])
    with pytest.raises(StructuredOutputError):
        call(make_client(fake))


def test_transient_errors_are_retried_then_succeed():
    fake = FakeAdapter().queue(
        "jd_parse", [TransientLLMError("429"), TransientLLMError("503"), {"label": "a", "score": 0}]
    )
    result = call(make_client(fake))
    assert result.attempts == 3


def test_transient_errors_exhaust_retries():
    fake = FakeAdapter().queue("jd_parse", [TransientLLMError("429")] * 3)
    with pytest.raises(TransientLLMError):
        call(make_client(fake, retries=2))


def test_provider_errors_are_not_retried():
    fake = FakeAdapter().queue("jd_parse", [LLMProviderError("401"), {"label": "a", "score": 0}])
    with pytest.raises(LLMProviderError):
        call(make_client(fake))
    assert len(fake.requests) == 1


def test_budget_stops_before_next_call():
    fake = FakeAdapter().queue("jd_parse", [{"label": "a", "score": 0}] * 2)
    client = make_client(fake, max_usd=0.0)
    with pytest.raises(BudgetExceeded):
        call(client)
    assert fake.requests == []


def test_meter_accumulates_cost_by_task():
    pricing = Pricing.load()
    meter = CostMeter(pricing)
    cost = meter.add("jd_parse", "anthropic", "claude-haiku-5-5", Usage(input_tokens=100_000))
    meter.add("jd_parse", "anthropic", "claude-haiku-5-5", Usage(output_tokens=1_000))
    assert cost == pytest.approx(0.01)
    assert meter.by_task["jd_parse"] == pytest.approx(0.01 + 0.0005)
    assert meter.calls == 2


def test_unknown_task_is_rejected():
    client = make_client(FakeAdapter())
    with pytest.raises(KeyError):
        client.structured("not_a_task", system="s", messages=[], schema=Answer)
