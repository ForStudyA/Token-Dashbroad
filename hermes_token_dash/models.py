"""Data models for Hermes Token Dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ModelPricing:
    """Pricing for a single model (per 1M tokens).

    Prices are stored in the model's native billing currency.
    ``currency`` is ``"USD"`` or ``"CNY"``.  USD prices are converted
    to CNY for display via ``EXCHANGE_RATE``; CNY prices are used as-is.
    """

    input_price: float
    output_price: float
    currency: str = "USD"
    cache_read_price: float = 0.0
    cache_write_price: float = 0.0

    def to_row(self, model_name: str) -> dict:
        return {
            "model": model_name,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "currency": self.currency,
            "cache_read_price": self.cache_read_price,
            "cache_write_price": self.cache_write_price,
        }

    def display_prices(self) -> tuple[float, float, float]:
        """Return (input, output, cache_read) prices in display currency (CNY)."""
        if self.currency == "CNY":
            return (self.input_price, self.output_price, self.cache_read_price)
        return (self.input_price * EXCHANGE_RATE, self.output_price * EXCHANGE_RATE,
                self.cache_read_price * EXCHANGE_RATE)

# Pricing per 1M tokens in native billing currency
# Sources: official API pricing pages (June 2026)
#   DeepSeek: api-docs.deepseek.com
#   Qwen: help.aliyun.com/zh/model-studio/model-pricing
#   GLM: open.bigmodel.cn/pricing
#   OpenAI: platform.openai.com/docs/pricing
#   Anthropic: anthropic.com/pricing
#   MiMo: help.aliyun.com (Aliyun-hosted pricing)
MODEL_PRICING: dict[str, ModelPricing] = {
    # DeepSeek (official domestic CNY)
    "deepseek-v4-pro": ModelPricing(3.00, 6.00, "CNY", cache_read_price=0.025),
    "deepseek-v4-flash": ModelPricing(1.00, 2.00, "CNY", cache_read_price=0.02),
    "deepseek-chat": ModelPricing(1.00, 2.00, "CNY", cache_read_price=0.02),  # 合并为 deepseek-v4-flash
    # MiMo / Xiaomi (priced same as DeepSeek equivalents)
    "mimo-v2.5": ModelPricing(1.00, 2.00, "CNY", cache_read_price=0.02),
    "mimo-v2.5-pro": ModelPricing(3.00, 6.00, "CNY", cache_read_price=0.025),
    # Qwen / Alibaba Cloud (domestic CNY)
    "qwen-max": ModelPricing(4.00, 10.00, "CNY"),
    "qwen-plus": ModelPricing(2.00, 8.00, "CNY"),
    "qwen-turbo": ModelPricing(0.30, 0.60, "CNY"),
    "qwen3-235b-a22b": ModelPricing(2.00, 8.00, "CNY"),
    # GLM / Zhipu (domestic CNY)
    "glm-5.2": ModelPricing(8.00, 28.00, "CNY", cache_read_price=2.00),
    # Claude / Anthropic (USD)
    "claude-sonnet-4-6": ModelPricing(3.00, 15.00, "USD"),
    "claude-opus-4-8": ModelPricing(5.00, 25.00, "USD"),
    # OpenAI / GPT (USD)
    "gpt-5.5": ModelPricing(5.00, 30.00, "USD", cache_read_price=0.50),
    "gpt-5.4-mini": ModelPricing(0.75, 4.50, "USD"),
    "gpt-5.3-codex": ModelPricing(1.25, 7.50, "USD"),
    "codex-auto-review": ModelPricing(0.00, 0.00, "USD"),
}

# Exchange rate: USD -> CNY (only applied to USD-priced models)
EXCHANGE_RATE: float = 7.0

DEFAULT_INPUT_PRICE = 0.50
DEFAULT_OUTPUT_PRICE = 2.00


def get_model_price(model: str) -> tuple[float, float, float]:
    """Return (input_price, output_price, cache_read_price) in CNY.

    Uses fuzzy substring matching.  USD models are converted via
    ``EXCHANGE_RATE``; CNY models are returned as-is.
    """
    model_lower = model.lower()
    for key, pricing in MODEL_PRICING.items():
        if key in model_lower or model_lower in key:
            return pricing.display_prices()
    return (DEFAULT_INPUT_PRICE, DEFAULT_OUTPUT_PRICE, 0.0)


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
        ``"deepseek-v4-pro"``             -> ``"deepseek"``
        ``"claude-sonnet-4-6-20250526"``  -> ``"claude"``
        ``"mimo-v2.5"``                   -> ``"mimo"``
        ``"unknown"``                     -> ``"unknown"``
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
    agent: str = ""
    api_call_count: int = 1  # API调用次数（Hermes从sessions表读取）


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
    cache_hit_rate: float = 0.0       # request-level: requests_with_cache / request_count
    token_hit_rate: float = 0.0       # token-level: total_cache_read / total_input
    estimated_cost: float = 0.0
    user_input_count: int = 0         # 用户输入次数

    def compute_derived(self) -> None:
        """Compute cache_hit_rate and estimated_cost from raw totals.

        Cache hit rate is defined as the percentage of total requests that
        had a cache read (cache_read_input_tokens > 0).
        Cost formula: (input - cache_read) * input_price
                    + cache_read * cache_read_price
                    + output * output_price
        (prices are per 1M tokens, already in CNY via get_model_price)
        """
        if self.request_count > 0:
            self.cache_hit_rate = (
                self.requests_with_cache / self.request_count * 100
            )
        if self.total_input > 0:
            self.token_hit_rate = (
                self.total_cache_read / self.total_input * 100
            )
        in_price, out_price, cr_price = get_model_price(self.model)
        non_cache_input = max(0, self.total_input - self.total_cache_read)
        self.estimated_cost = (
            non_cache_input / 1_000_000 * in_price
            + self.total_cache_read / 1_000_000 * cr_price
            + self.total_output / 1_000_000 * out_price
        )
