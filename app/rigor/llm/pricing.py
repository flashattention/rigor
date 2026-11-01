"""토큰 → 비용 계산과 사전 추정. 단가는 configs/pricing.yaml (확인 날짜와 함께)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from rigor.llm.types import Usage

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "configs" / "pricing.yaml"


@dataclass(frozen=True)
class Tier:
    max_prompt_tokens: int | None
    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class ModelPrice:
    tiers: tuple[Tier, ...]
    verified: bool
    tokenizer_factor: float = 1.0  # 같은 텍스트에 대한 토큰 증가 배수 (추정용)

    def tier_for(self, prompt_tokens: int) -> Tier:
        for tier in self.tiers:
            if tier.max_prompt_tokens is None or prompt_tokens <= tier.max_prompt_tokens:
                return tier
        return self.tiers[-1]


class Pricing:
    def __init__(self, models: dict[tuple[str, str], ModelPrice], krw_per_usd: float):
        self.models = models
        self.krw_per_usd = krw_per_usd

    def price(self, provider: str, model: str) -> ModelPrice:
        try:
            return self.models[(provider, model)]
        except KeyError as exc:
            raise KeyError(f"no price for {provider}/{model} in pricing.yaml") from exc

    def cost_usd(self, provider: str, model: str, usage: Usage) -> float:
        """요청 1건의 비용. 단가 구간은 요청의 프롬프트 길이로 정한다."""
        tier = self.price(provider, model).tier_for(usage.prompt_tokens)
        per_token = 1 / 1_000_000
        return per_token * (
            usage.input_tokens * tier.input
            + usage.output_tokens * tier.output
            + usage.cache_read_tokens * tier.cache_read
            + usage.cache_write_tokens * tier.cache_write
        )

    def estimate_usd(
        self,
        provider: str,
        model: str,
        *,
        runs: float,
        input_tokens_per_run: int,
        output_tokens_per_run: int,
        batch: bool = False,
    ) -> float:
        """실행 전 추정 (docs/cost-estimate.md의 계산과 같다). 요청마다 하위 구간이라고 가정."""
        price = self.price(provider, model)
        tier = price.tiers[0]
        f = price.tokenizer_factor
        per_run = (
            input_tokens_per_run * f * tier.input + output_tokens_per_run * f * tier.output
        ) / 1_000_000
        return per_run * runs * (0.5 if batch else 1.0)

    def to_krw(self, usd: float) -> float:
        return usd * self.krw_per_usd

    @classmethod
    def load(cls, path: Path | None = None) -> Pricing:
        data: dict[str, Any] = yaml.safe_load((path or DEFAULT_PATH).read_text(encoding="utf-8"))
        models: dict[tuple[str, str], ModelPrice] = {}
        for provider, entries in data["usd_per_mtok"].items():
            for model, spec in entries.items():
                tiers = tuple(Tier(**t) for t in spec["tiers"])
                models[(provider, model)] = ModelPrice(
                    tiers=tiers,
                    verified=bool(spec.get("verified", False)),
                    tokenizer_factor=float(spec.get("tokenizer_factor", 1.0)),
                )
        return cls(models, float(data["krw_per_usd"]))
