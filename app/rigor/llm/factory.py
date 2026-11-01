"""설정에서 LLMClient를 조립한다. 노드·CLI·평가 러너가 같은 조립 경로를 쓴다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from rigor.llm.adapters.fake import FakeAdapter
from rigor.llm.adapters.recorded import DEFAULT_DIR, RecordedAdapter
from rigor.llm.budget import CostMeter
from rigor.llm.client import LLMClient
from rigor.llm.pricing import Pricing
from rigor.llm.routing import Routing
from rigor.llm.types import LLMAdapter

Mode = Literal["live", "record", "replay"]


def _live_adapter(provider: str) -> LLMAdapter:
    if provider == "openai":
        from rigor.llm.adapters.openai_adapter import OpenAIAdapter

        return OpenAIAdapter()
    if provider == "anthropic":
        from rigor.llm.adapters.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter()
    raise ValueError(f"no live adapter for provider {provider}")


def build_client(
    *,
    profile: str | None = None,
    mode: Mode = "replay",
    max_usd: float | None = None,
    fake: FakeAdapter | None = None,
    recordings_dir: Path = DEFAULT_DIR,
) -> LLMClient:
    """mode: live = 실제 호출 / record = 실제 호출 + 녹화 / replay = 녹화만 (네트워크 없음)."""
    routing = Routing.load(profile=profile)
    providers = {routing.resolve(t).provider for t in routing.tasks} | {routing.default.provider}
    adapters: dict[str, LLMAdapter] = {}
    for provider in providers:
        if provider == "fake":
            adapters[provider] = fake or FakeAdapter()
        elif mode == "live":
            adapters[provider] = _live_adapter(provider)
        elif mode == "record":
            adapters[provider] = RecordedAdapter(
                provider, "record", _live_adapter(provider), recordings_dir
            )
        else:
            adapters[provider] = RecordedAdapter(provider, "replay", directory=recordings_dir)
    return LLMClient(routing, adapters, CostMeter(Pricing.load(), max_usd=max_usd))
