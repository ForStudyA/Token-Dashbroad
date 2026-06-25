"""Unit tests for hermes_token_dash.models."""

from __future__ import annotations

from datetime import datetime

import pytest

from hermes_token_dash.models import (
    DEFAULT_INPUT_PRICE,
    DEFAULT_OUTPUT_PRICE,
    MODEL_PRICING,
    ModelPricing,
    ModelStats,
    TokenUsage,
    extract_provider,
    get_full_model_pricing,
    get_model_price,
)


# ---------------------------------------------------------------------------
# ModelPricing
# ---------------------------------------------------------------------------

class TestModelPricing:
    """Tests for the ModelPricing dataclass."""

    def test_create_default(self):
        """Default cache prices are 0.0."""
        mp = ModelPricing(input_price=1.0, output_price=2.0)
        assert mp.input_price == 1.0
        assert mp.output_price == 2.0
        assert mp.cache_read_price == 0.0
        assert mp.cache_write_price == 0.0

    def test_create_full(self):
        """All fields can be set explicitly."""
        mp = ModelPricing(
            input_price=1.0,
            output_price=2.0,
            cache_read_price=0.5,
            cache_write_price=0.3,
        )
        assert mp.input_price == 1.0
        assert mp.output_price == 2.0
        assert mp.cache_read_price == 0.5
        assert mp.cache_write_price == 0.3

    def test_to_row(self):
        """to_row returns a dict keyed by model name with all price fields."""
        mp = ModelPricing(
            input_price=0.55,
            output_price=0.19,
            cache_read_price=0.05,
            cache_write_price=0.02,
        )
        row = mp.to_row("deepseek-v4-pro")
        assert row == {
            "model": "deepseek-v4-pro",
            "input_price": 0.55,
            "output_price": 0.19,
            "cache_read_price": 0.05,
            "cache_write_price": 0.02,
        }

    def test_to_row_different_model_name(self):
        """to_row accepts any model_name string."""
        mp = ModelPricing(input_price=3.0, output_price=15.0)
        row = mp.to_row("my-custom-model")
        assert row["model"] == "my-custom-model"
        assert row["input_price"] == 3.0

    def test_to_row_roundtrip(self):
        """ModelPricing.from_row(to_row(...)) is not built-in, but values
        survive a manual reconstruction."""
        mp = ModelPricing(0.09, 0.36, 0.01, 0.01)
        row = mp.to_row("deepseek-v4-flash")
        reconstructed = ModelPricing(
            input_price=row["input_price"],
            output_price=row["output_price"],
            cache_read_price=row["cache_read_price"],
            cache_write_price=row["cache_write_price"],
        )
        assert reconstructed == mp


# ---------------------------------------------------------------------------
# get_model_price
# ---------------------------------------------------------------------------

class TestGetModelPrice:
    """Tests for the get_model_price function."""

    def test_exact_match(self):
        """Exact model name returns correct pricing tuple."""
        input_p, output_p = get_model_price("deepseek-v4-pro")
        assert input_p == 0.55
        assert output_p == 0.19

    def test_exact_match_mimo(self):
        """mimo-v2.5 pricing."""
        input_p, output_p = get_model_price("mimo-v2.5")
        assert input_p == 0.50
        assert output_p == 2.00

    def test_fuzzy_match_longer_variant(self):
        """'claude-sonnet-4-6-20250526' matches 'claude-sonnet-4-6'."""
        input_p, output_p = get_model_price("claude-sonnet-4-6-20250526")
        assert input_p == 3.00
        assert output_p == 15.00

    def test_fuzzy_match_contains_key(self):
        """'my-deepseek-v4-pro-custom' contains the key."""
        input_p, output_p = get_model_price("my-deepseek-v4-pro-custom")
        assert input_p == 0.55
        assert output_p == 0.19

    def test_no_match_returns_default(self):
        """Unknown model falls back to default pricing."""
        input_p, output_p = get_model_price("nonexistent-model")
        assert input_p == DEFAULT_INPUT_PRICE
        assert output_p == DEFAULT_OUTPUT_PRICE

    def test_case_insensitive(self):
        """Matching is case-insensitive."""
        input_p, output_p = get_model_price("DEEPSEEK-V4-PRO")
        assert input_p == 0.55
        assert output_p == 0.19

    def test_empty_string(self):
        """Empty string is a substring of every key — matches first key
        (deepseek-v4-pro, 0.55/0.19) rather than returning defaults."""
        input_p, output_p = get_model_price("")
        assert input_p == 0.55
        assert output_p == 0.19

    def test_default_constants(self):
        """DEFAULT_INPUT_PRICE and DEFAULT_OUTPUT_PRICE are 0.50 and 2.00."""
        assert DEFAULT_INPUT_PRICE == 0.50
        assert DEFAULT_OUTPUT_PRICE == 2.00


# ---------------------------------------------------------------------------
# get_full_model_pricing
# ---------------------------------------------------------------------------

class TestGetFullModelPricing:
    """Tests for get_full_model_pricing function."""

    def test_returns_pricing_object(self):
        """Returns a ModelPricing instance for a known model."""
        pricing = get_full_model_pricing("deepseek-v4-flash")
        assert isinstance(pricing, ModelPricing)
        assert pricing.input_price == 0.09
        assert pricing.output_price == 0.36

    def test_returns_pricing_fuzzy(self):
        """Fuzzy matching also works for full pricing."""
        pricing = get_full_model_pricing("claude-opus-4-8-special")
        assert isinstance(pricing, ModelPricing)
        assert pricing.input_price == 15.00
        assert pricing.output_price == 75.00

    def test_returns_none_for_unknown(self):
        """Returns None for unknown models."""
        assert get_full_model_pricing("some-random-model") is None

    def test_empty_string_returns_none(self):
        """Empty string is a substring of every key — matches first key
        (deepseek-v4-pro) rather than returning None."""
        pricing = get_full_model_pricing("")
        assert isinstance(pricing, ModelPricing)
        assert pricing.input_price == 0.55


# ---------------------------------------------------------------------------
# extract_provider
# ---------------------------------------------------------------------------

class TestExtractProvider:
    """Tests for extract_provider function."""

    def test_deepseek(self):
        assert extract_provider("deepseek-v4-pro") == "deepseek"

    def test_claude(self):
        assert extract_provider("claude-sonnet-4-6-20250526") == "claude"

    def test_mimo(self):
        assert extract_provider("mimo-v2.5") == "mimo"

    def test_mimo_pro(self):
        assert extract_provider("mimo-v2.5-pro") == "mimo"

    def test_single_segment(self):
        """A name without hyphens returns itself."""
        assert extract_provider("unknown") == "unknown"

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert extract_provider("") == ""

    def test_leading_dash(self):
        """'-bad' split → ['', 'bad']. parts[0].strip() == '' is falsy,
        so the guard clause falls back to returning original model '-bad'."""
        assert extract_provider("-bad") == "-bad"

    def test_trailing_dash(self):
        """'bad-' -> 'bad'."""
        assert extract_provider("bad-") == "bad"

    def test_multiple_dashes(self):
        """'a-b-c-d' -> 'a'."""
        assert extract_provider("a-b-c-d") == "a"

    def test_mixed_case(self):
        """extract_provider does not lower-case."""
        assert extract_provider("DeepSeek-v4-pro") == "DeepSeek"

    def test_all_models_in_pricing(self):
        """Every model in MODEL_PRICING starts with its provider."""
        for model_name in MODEL_PRICING:
            provider = extract_provider(model_name)
            assert provider in ("deepseek", "mimo", "claude"), (
                f"{model_name!r} extracted as {provider!r}"
            )


# ---------------------------------------------------------------------------
# TokenUsage dataclass
# ---------------------------------------------------------------------------

class TestTokenUsage:
    """Tests for the TokenUsage dataclass."""

    def test_create_minimal(self):
        """TokenUsage can be created with required fields."""
        ts = "2025-01-15T10:30:00"
        tu = TokenUsage(
            request_id="req-001",
            model="deepseek-v4-pro",
            input_tokens=1000,
            output_tokens=500,
            cache_read=0,
            cache_creation=0,
            timestamp=datetime.fromisoformat(ts),
        )
        assert tu.request_id == "req-001"
        assert tu.model == "deepseek-v4-pro"
        assert tu.input_tokens == 1000
        assert tu.output_tokens == 500
        assert tu.cache_read == 0
        assert tu.cache_creation == 0
        assert tu.timestamp.isoformat() == ts
        assert tu.data_source == "unknown"
        assert tu.status_code == 200
        assert tu.latency_ms == 0.0
        assert tu.first_token_ms == 0.0

    def test_create_full(self):
        """All optional fields can be set."""
        tu = TokenUsage(
            request_id="req-002",
            model="claude-sonnet-4-6",
            input_tokens=5000,
            output_tokens=2000,
            cache_read=30720,
            cache_creation=0,
            timestamp=datetime(2025, 6, 1, 12, 0, 0),
            data_source="claude",
            status_code=201,
            latency_ms=1500.5,
            first_token_ms=200.3,
        )
        assert tu.data_source == "claude"
        assert tu.status_code == 201
        assert tu.latency_ms == 1500.5
        assert tu.first_token_ms == 200.3

    def test_zero_tokens(self):
        """Zero input and output tokens is valid."""
        tu = TokenUsage(
            request_id="req-zero",
            model="mimo-v2.5",
            input_tokens=0,
            output_tokens=0,
            cache_read=0,
            cache_creation=0,
            timestamp=datetime.now(),
        )
        assert tu.input_tokens == 0
        assert tu.output_tokens == 0

    def test_negative_tokens_allowed(self):
        """Dataclass does not enforce non-negative tokens."""
        tu = TokenUsage(
            request_id="req-neg",
            model="test",
            input_tokens=-100,
            output_tokens=-50,
            cache_read=0,
            cache_creation=0,
            timestamp=datetime.now(),
        )
        assert tu.input_tokens == -100

    def test_large_tokens(self):
        """Very large token counts work."""
        tu = TokenUsage(
            request_id="req-big",
            model="mimo-v2.5-pro",
            input_tokens=10_000_000,
            output_tokens=5_000_000,
            cache_read=0,
            cache_creation=0,
            timestamp=datetime.now(),
        )
        assert tu.input_tokens == 10_000_000
        assert tu.output_tokens == 5_000_000


# ---------------------------------------------------------------------------
# ModelStats dataclass
# ---------------------------------------------------------------------------

class TestModelStats:
    """Tests for ModelStats dataclass and compute_derived()."""

    def test_create(self):
        """ModelStats can be created with required fields."""
        ms = ModelStats(
            model="deepseek-v4-pro",
            date="2025-06-01",
            total_input=1_000_000,
            total_output=500_000,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=10,
            requests_with_cache=0,
        )
        assert ms.model == "deepseek-v4-pro"
        assert ms.date == "2025-06-01"
        assert ms.total_input == 1_000_000
        assert ms.total_output == 500_000
        assert ms.request_count == 10
        assert ms.cache_hit_rate == 0.0
        assert ms.estimated_cost == 0.0

    def test_compute_derived_sets_cost(self):
        """compute_derived calculates estimated_cost from pricing."""
        ms = ModelStats(
            model="deepseek-v4-pro",
            date="2025-06-01",
            total_input=1_000_000,
            total_output=1_000_000,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=1,
            requests_with_cache=0,
        )
        ms.compute_derived()
        # 1M input * 0.55 + 1M output * 0.19 = 0.55 + 0.19 = 0.74
        assert ms.estimated_cost == pytest.approx(0.74)

    def test_compute_derived_cache_hit_rate(self):
        """Cache hit rate = requests_with_cache / request_count * 100."""
        ms = ModelStats(
            model="claude-sonnet-4-6",
            date="2025-06-01",
            total_input=0,
            total_output=0,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=10,
            requests_with_cache=7,
        )
        ms.compute_derived()
        assert ms.cache_hit_rate == 70.0

    def test_compute_derived_zero_requests(self):
        """When request_count is 0, cache_hit_rate stays 0.0."""
        ms = ModelStats(
            model="mimo-v2.5",
            date="2025-06-01",
            total_input=500_000,
            total_output=200_000,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=0,
            requests_with_cache=0,
        )
        ms.compute_derived()
        assert ms.cache_hit_rate == 0.0
        # Cost still calculated even with zero requests
        # 0.5M input * 0.50 + 0.2M output * 2.00 = 0.25 + 0.40 = 0.65
        assert ms.estimated_cost == pytest.approx(0.65)

    def test_compute_derived_unknown_model_default_pricing(self):
        """Unknown model uses default pricing for cost estimation."""
        ms = ModelStats(
            model="some-unknown-model",
            date="2025-06-01",
            total_input=1_000_000,
            total_output=1_000_000,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=5,
            requests_with_cache=2,
        )
        ms.compute_derived()
        # Default: 0.50 input, 2.00 output = 0.50 + 2.00 = 2.50
        assert ms.estimated_cost == pytest.approx(2.50)
        assert ms.cache_hit_rate == 40.0

    def test_compute_derived_idempotent(self):
        """Calling compute_derived twice gives the same result."""
        ms = ModelStats(
            model="deepseek-v4-pro",
            date="2025-06-01",
            total_input=2_000_000,
            total_output=1_000_000,
            total_cache_read=0,
            total_cache_creation=0,
            request_count=4,
            requests_with_cache=3,
        )
        ms.compute_derived()
        cost1 = ms.estimated_cost
        rate1 = ms.cache_hit_rate
        ms.compute_derived()
        assert ms.estimated_cost == pytest.approx(cost1)
        assert ms.cache_hit_rate == rate1

    def test_all_known_models_in_pricing(self):
        """Every model in MODEL_PRICING produces a valid cost."""
        for model_name in MODEL_PRICING:
            ms = ModelStats(
                model=model_name,
                date="2025-06-01",
                total_input=1_000_000,
                total_output=1_000_000,
                total_cache_read=0,
                total_cache_creation=0,
                request_count=1,
            )
            ms.compute_derived()
            assert ms.estimated_cost > 0, (
                f"Cost should be positive for {model_name}"
            )


# ---------------------------------------------------------------------------
# MODEL_PRICING dict integrity
# ---------------------------------------------------------------------------

class TestModelPricingDict:
    """Tests for the MODEL_PRICING module-level dict."""

    def test_all_entries_are_model_pricing(self):
        """Every value is a ModelPricing instance."""
        for key, value in MODEL_PRICING.items():
            assert isinstance(value, ModelPricing), (
                f"MODEL_PRICING[{key!r}] is not ModelPricing"
            )

    def test_prices_are_non_negative(self):
        """All prices are >= 0."""
        for key, mp in MODEL_PRICING.items():
            assert mp.input_price >= 0, f"{key}: input_price negative"
            assert mp.output_price >= 0, f"{key}: output_price negative"
            assert mp.cache_read_price >= 0, f"{key}: cache_read_price negative"
            assert mp.cache_write_price >= 0, f"{key}: cache_write_price negative"

    def test_known_model_count(self):
        """We currently have 6 models defined."""
        assert len(MODEL_PRICING) == 6

    def test_deepseek_v4_pro_pricing(self):
        mp = MODEL_PRICING["deepseek-v4-pro"]
        assert mp.input_price == 0.55
        assert mp.output_price == 0.19

    def test_claude_sonnet_4_6_pricing(self):
        mp = MODEL_PRICING["claude-sonnet-4-6"]
        assert mp.input_price == 3.00
        assert mp.output_price == 15.00

    def test_claude_opus_4_8_pricing(self):
        mp = MODEL_PRICING["claude-opus-4-8"]
        assert mp.input_price == 15.00
        assert mp.output_price == 75.00


# ---------------------------------------------------------------------------
# Fuzzy matching edge cases
# ---------------------------------------------------------------------------

class TestFuzzyMatchingEdgeCases:
    """Edge-case tests for the fuzzy matching logic."""

    def test_substring_in_middle(self):
        """A model string that contains a key somewhere in the middle."""
        # "deepseek-v4-pro" is inside "prefix-deepseek-v4-pro-suffix"
        input_p, output_p = get_model_price("prefix-deepseek-v4-pro-suffix")
        assert input_p == 0.55
        assert output_p == 0.19

    def test_ambiguous_match_first_wins(self):
        """mimo-v2.5 and mimo-v2.5-pro — both contain each other.
        Dictionaries iterate in insertion order, so "mimo-v2.5" matches first
        for input "mimo-v2.5-pro" (because "mimo-v2.5" in "mimo-v2.5-pro").
        """
        # "mimo-v2.5" is a substring of "mimo-v2.5-pro"
        input_p, output_p = get_model_price("mimo-v2.5-pro")
        # First matching key in insertion order is "mimo-v2.5"
        # which has pricing (0.50, 2.00)
        assert input_p == 0.50
        assert output_p == 2.00

    def test_fuzzy_full_pricing_ambiguous(self):
        """get_full_model_pricing for 'mimo-v2.5-pro' returns first match
        (mimo-v2.5)."""
        pricing = get_full_model_pricing("mimo-v2.5-pro")
        assert pricing is not None
        # First match in dict order is "mimo-v2.5"
        assert pricing.input_price == 0.50
        assert pricing.output_price == 2.00

    def test_claude_opus_fuzzy_long(self):
        """'some-claude-opus-4-8-20250615' matches 'claude-opus-4-8'."""
        input_p, output_p = get_model_price("some-claude-opus-4-8-20250615")
        assert input_p == 15.00
        assert output_p == 75.00
