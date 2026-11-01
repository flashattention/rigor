"""실행 중 비용 누적과 상한. 상한을 넘으면 다음 호출 전에 멈춘다."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from rigor.llm.pricing import Pricing
from rigor.llm.types import BudgetExceeded, Usage


@dataclass
class CostMeter:
    pricing: Pricing
    max_usd: float | None = None
    total_usd: float = 0.0
    by_task: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    usage_by_task: dict[str, Usage] = field(default_factory=lambda: defaultdict(Usage))
    calls: int = 0

    def check(self) -> None:
        if self.max_usd is not None and self.total_usd >= self.max_usd:
            raise BudgetExceeded(
                f"budget exhausted: spent ${self.total_usd:.4f} of ${self.max_usd:.4f}"
            )

    def add(self, task: str, provider: str, model: str, usage: Usage) -> float:
        cost = self.pricing.cost_usd(provider, model, usage)
        self.total_usd += cost
        self.by_task[task] += cost
        self.usage_by_task[task] = self.usage_by_task[task] + usage
        self.calls += 1
        return cost
