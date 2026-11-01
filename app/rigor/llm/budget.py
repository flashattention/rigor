"""실행 중 비용 누적과 상한. 상한을 넘으면 다음 호출 전에 멈춘다."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rigor.llm.pricing import Pricing
from rigor.llm.types import BudgetExceeded, Usage


@dataclass
class CostMeter:
    """LLMClient 하나의 누적 비용. max_usd가 None이면 상한이 없다."""

    pricing: Pricing
    max_usd: float | None = None
    total_usd: float = 0.0
    by_task: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    usage_by_task: dict[str, Usage] = field(default_factory=lambda: defaultdict(Usage))
    calls: int = 0

    def check(self) -> None:
        """이미 쓴 비용만 본다. 호출 1건이 상한을 넘기는 것은 막지 못한다."""
        if self.max_usd is not None and self.total_usd >= self.max_usd:
            raise BudgetExceeded(
                f"budget exhausted: spent ${self.total_usd:.4f} of ${self.max_usd:.4f}"
            )

    def add(self, task: str, provider: str, model: str, usage: Usage) -> float:
        """사용량을 누적하고 이번 호출의 비용을 돌려준다."""
        cost = self.pricing.cost_usd(provider, model, usage)
        self.total_usd += cost
        self.by_task[task] += cost
        self.usage_by_task[task] = self.usage_by_task[task] + usage
        self.calls += 1
        return cost
