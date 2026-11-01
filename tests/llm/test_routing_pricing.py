import pytest

from rigor.llm.pricing import Pricing
from rigor.llm.routing import KNOWN_TASKS, Route, Routing
from rigor.llm.types import Usage


def test_default_profile_is_fake_so_tests_never_hit_network():
    routing = Routing.load()
    assert routing.profile_name == "fake"
    assert {routing.resolve(t).provider for t in KNOWN_TASKS} == {"fake"}


def test_env_overrides_profile(monkeypatch):
    monkeypatch.setenv("RIGOR_ROUTING_PROFILE", "anthropic_haiku")
    routing = Routing.load()
    assert routing.resolve("requirement_verdict") == Route(
        "anthropic", "claude-haiku-5-5", {"max_tokens": 8000, "temperature": 0}
    )


def test_hash_changes_when_any_task_route_changes():
    base = Routing("p", Route("anthropic", "claude-haiku-5-5"), {})
    upgraded = Routing(
        "p",
        Route("anthropic", "claude-haiku-5-5"),
        {"requirement_verdict": Route("anthropic", "claude-sonnet-5-5")},
    )
    assert base.hash != upgraded.hash
    assert base.hash == Routing("p", Route("anthropic", "claude-haiku-5-5"), {}).hash


def test_unknown_task_in_profile_is_rejected():
    with pytest.raises(ValueError):
        Routing("p", Route("fake", "fake-1"), {"typo_task": Route("fake", "fake-1")})


def test_every_routed_model_has_a_price():
    pricing = Pricing.load()
    for profile in ("fake", "openai_phase1", "anthropic_haiku"):
        routing = Routing.load(profile=profile)
        for task in KNOWN_TASKS:
            r = routing.resolve(task)
            pricing.price(r.provider, r.model)


def test_haiku_long_prompt_uses_higher_tier():
    pricing = Pricing.load()
    short = pricing.cost_usd("anthropic", "claude-haiku-5-5", Usage(input_tokens=100_000))
    long = pricing.cost_usd("anthropic", "claude-haiku-5-5", Usage(input_tokens=100_001))
    assert short == pytest.approx(0.01)
    assert long == pytest.approx(100_001 * 0.50 / 1_000_000)


def test_estimate_matches_cost_estimate_doc():
    # docs/cost-estimate.md: Haiku 5.5 케이스 1건 ≈ ₩35, Batch ≈ ₩17, 전체 평가(170) ≈ ₩6,000
    pricing = Pricing.load()
    per_case = pricing.estimate_usd(
        "anthropic",
        "claude-haiku-5-5",
        runs=1,
        input_tokens_per_run=110_000,
        output_tokens_per_run=16_000,
    )
    assert pricing.to_krw(per_case) == pytest.approx(35, abs=1)
    full = pricing.estimate_usd(
        "anthropic",
        "claude-haiku-5-5",
        runs=170,
        input_tokens_per_run=110_000,
        output_tokens_per_run=16_000,
    )
    assert pricing.to_krw(full) == pytest.approx(5_900, rel=0.05)
