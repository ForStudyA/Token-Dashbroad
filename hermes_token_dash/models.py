"""Data models for Hermes Token Dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ModelPricing:
    """Pricing for a single model (per 1M tokens, USD)."""

    input_price: float
    output_price: float
    cache_read_price: float = 0.0
    cache_write_price: float = 0.0

    def to_row(self, model_name: str) -> dict:
        return {
            "model": model_name,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "cache_read_price": self.cache_read_price,
            "cache_write_price": self.cache_write_price,
        }


# Pricing per 1M tokens (input, output, cache_read, cache_write) in USD
MODEL_PRICING: dict[str, ModelPricing] = {
    "deepseek-v4-pro": ModelPricing(0.55, 0.19),
    "deepseek-v4-flash": ModelPricing(0.09, 0.36),
    "mimo-v2.5": ModelPricing(0.50, 2.00),
    "mimo-v2.5-pro": ModelPricing(0.50, 2.00),
    "claude-sonnet-4-6": ModelPricing(3.00, 15.00),
    "claude-opus-4-8": ModelPricing(15.00, 75.00),
}

DEFAULT_INPUT_PRICE = 0.50
DEFAULT_OUTPUT_PRICE = 2.00


def get_model_price(model: str) -> tuple[float, float]:
    """Return (input_price_per_1M, output_price_per_1M) for a model.

    Uses fuzzy substring matching so that model name variants like
    ``claude-sonnet-4-6-20250526`` still hit the right pricing entry.
    """
    model_lower = model.lower()
    for key, prices in MODEL_PRICING.items():
        if key in model_lower or model_lower in key:
            return (prices.input_price, prices.output_price)
    return (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE)


def get_full_model_pricing(model: str) -> ModelPricing | None:
    """Return the full ``ModelPricing`` entry for *model*, or *None*.

    Uses the same fuzzy matching as ``get_model_price``.
    """
    model_lower = model.lower()
    for key, pricing in MODEL_PRICING.items():
        if key in model_lower or model_lower in key:
            return pricing
    return None


def extract_provider(model: str) -> str:
    """Extract a short provider name from a model identifier.

    Splits on ``-`` and returns the first segment when it looks like a
    meaningful name.  Falls back to the raw model string.

    Examples:
        ``"deepseek-v4-pro"``          → ``"deepseek"``
        ``"claude-sonnet-4-6-20250526"`` → ``"claude"``
        ``"mimo-v2.5"``                → ``"mimo"``
        ``"unknown"``                  → ``"unknown"``
    """
    parts = model.split("-")
    if parts and parts[0].strip():
        return parts[0].strip()
    return model


@dataclass
class TokenUsage:
    """A single deduplicated token usage record from one request."""

    request_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_creation: int
    timestamp: datetime
    data_source: str = "unknown"
    status_code: int = 200
    latency_ms: float = 0.0
    first_token_ms: float = 0.0
    profile: str = ""


@dataclass
class ModelStats:
    """Aggregated token statistics for one model on one date."""

    model: str
    date: str  # YYYY-MM-DD
    total_input: int
    total_output: int
    total_cache_read: int
    total_cache_creation: int
    request_count: int
    requests_with_cache: int = 0
    cache_hit_rate: float = 0.0
    estimated_cost: float = 0.0

    def compute_derived(self) -> None:
        """Compute cache_hit_rate and estimated_cost from raw totals.

        Cache hit rate is defined as the percentage of total requests that
        had a cache read (cache_read_input_tokens > 0).
        """
        if self.request_count > 0:
            self.cache_hit_rate = (
                self.requests_with_cache / self.request_count * 100
            )
        in_price, out_price = get_model_price(self.model)
        self.estimated_cost = (
            self.total_input / 1_000_000 * in_price
            + self.total_output / 1_000_000 * out_price
        )
