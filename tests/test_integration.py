"""Integration tests for hermes-token-dash cross-module flows.

Scenarios:
  S1 — Full pipeline: parse → aggregate → verify stats (parser + models)
  S2 — HTTP request flow: parser → server → JSON response (parser + server + models)
  S3 — Multi-source merge + pricing integration (all modules)
"""

from datetime import datetime, timezone

from hermes_token_dash.models import ModelStats, TokenUsage, get_model_price


# ═══════════════════════════════════════════════════════════════════════
# S1 — Full pipeline: parse → aggregate → verify stats
# ═══════════════════════════════════════════════════════════════════════

class TestParseToAggregate:
    """Integration: parser_claude → models aggregation."""

    def test_parse_jsonl_yields_correct_record_count(self, claude_records):
        """Scenario 1a — parse_jsonl returns correct number of non-synthetic,
        non-empty records with streaming dedup applied."""
        assert len(claude_records) == 3, f"Expected 3, got {len(claude_records)}"
        req_ids = {r.request_id for r in claude_records}
        assert req_ids == {"req-001", "req-002", "req-003"}

    def test_streaming_dedup_keeps_max_tokens(self, claude_records):
        """Scenario 1b — streaming duplicate (req-001 has 2 lines with
        different token counts); parser keeps the maximum of each field."""
        req1 = next(r for r in claude_records if r.request_id == "req-001")
        assert req1.input_tokens == 2000, f"Expected 2000, got {req1.input_tokens}"
        assert req1.output_tokens == 1000, f"Expected 1000, got {req1.output_tokens}"
        assert req1.cache_read == 300, f"Expected 300, got {req1.cache_read}"
        assert req1.data_source == "claude"

    def test_parse_discards_synthetic_and_user_types(self, claude_records):
        """Scenario 1c — records with model='<synthetic>' or type='user'
        are discarded by the parser."""
        synths = [r for r in claude_records if r.request_id == "req-synth"]
        users = [r for r in claude_records if r.request_id == "req-999"]
        assert len(synths) == 0
        assert len(users) == 0

    def test_aggregate_by_model_date_structure(self, claude_records):
        """Scenario 1d — aggregate_by_model_date produces ModelStats with
        correct shape when called with default (all) filter."""
        from hermes_token_dash.parser_claude import aggregate_by_model_date

        stats = aggregate_by_model_date(claude_records, time_filter="all")
        assert isinstance(stats, list)
        assert len(stats) == 3  # 3 unique (model, date) combos
        for s in stats:
            assert isinstance(s, ModelStats)
            assert isinstance(s.model, str)
            assert isinstance(s.date, str)
            assert s.total_input >= 0
            assert s.total_output >= 0
            assert s.request_count > 0

    def test_aggregate_totals_correct(self, claude_records):
        """Scenario 1e — verify aggregated token totals across all records."""
        from hermes_token_dash.parser_claude import aggregate_by_model_date

        stats = aggregate_by_model_date(claude_records, time_filter="all")
        total_input = sum(s.total_input for s in stats)
        total_output = sum(s.total_output for s in stats)
        total_requests = sum(s.request_count for s in stats)

        assert total_input == 5500  # 2000+3000+500
        assert total_output == 4700  # 1000+1200+2500
        assert total_requests == 3

    def test_time_filter_30d_includes_all_test_data(self, claude_records):
        """Scenario 1f — time_filter='30d' includes all test records
        (they are within 30 days of now)."""
        from hermes_token_dash.parser_claude import aggregate_by_model_date

        stats = aggregate_by_model_date(claude_records, time_filter="30d")
        assert len(stats) == 3

    def test_model_stats_derived_fields(self, claude_records):
        """Scenario 1g — ModelStats.compute_derived fills cache_hit_rate
        and estimated_cost from raw totals."""
        from hermes_token_dash.parser_claude import aggregate_by_model_date

        stats = aggregate_by_model_date(claude_records)
        # deepseek-v4-pro: should have cache_hit > 0
        deepseek_stat = [s for s in stats if s.model == "deepseek-v4-pro"]
        assert len(deepseek_stat) > 0
        for s in deepseek_stat:
            assert s.cache_hit_rate > 0
            assert s.estimated_cost > 0

    def test_get_available_models_unique_sorted(self, claude_records):
        """Scenario 1h — get_available_models returns sorted unique model names."""
        from hermes_token_dash.parser_claude import get_available_models

        models = get_available_models(claude_records)
        assert models == [
            "claude-sonnet-4-6-20250526",
            "deepseek-v4-pro",
            "mimo-v2.5",
        ]


# ═══════════════════════════════════════════════════════════════════════
# S2 — HTTP request flow: parser → server → JSON response
# ═══════════════════════════════════════════════════════════════════════

class TestServerIntegration:
    """Integration: parser → FastAPI server → HTTP response."""

    def test_api_stats_returns_expected_structure(self, test_client):
        """Scenario 2a — GET /api/stats returns a JSON array with correct keys."""
        resp = test_client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for item in data:
            assert "model" in item
            assert "date" in item
            assert "input" in item
            assert "output" in item
            assert "requests" in item
            assert "cost" in item

    def test_api_stats_model_filter(self, test_client):
        """Scenario 2b — GET /api/stats?model=... filters to matching records."""
        resp = test_client.get("/api/stats?model=deepseek-v4-pro")
        assert resp.status_code == 200
        data = resp.json()
        for item in data:
            assert item["model"] == "deepseek-v4-pro"

    def test_api_stats_source_filter(self, test_client):
        """Scenario 2c — GET /api/stats?source=claude returns only Claude records."""
        resp = test_client.get("/api/stats?source=claude")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0

        # All should be from Claude (check via model names — only Claude data has these)
        claude_models = {"deepseek-v4-pro", "claude-sonnet-4-6-20250526", "mimo-v2.5"}
        for item in data:
            assert item["model"] in claude_models, (
                f"Expected Claude model, got {item['model']}"
            )

    def test_api_summary_totals(self, test_client):
        """Scenario 2d — GET /api/summary returns aggregated totals matching
        known test data."""
        resp = test_client.get("/api/summary")
        assert resp.status_code == 200
        summary = resp.json()
        assert "input" in summary
        assert "output" in summary
        assert "requests" in summary
        assert "cost" in summary
        assert "groups" in summary

        # Claude: 2000+3000+500=5500 input, 1000+1200+2500=4700 output, 3 requests
        # Hermes: 2000+800=2800 input, 1000+3200=4200 output, 2 requests
        # Total: 8300 input, 8900 output, 5 requests
        assert summary["input"] == 8300, f"Expected 8300, got {summary['input']}"
        assert summary["output"] == 8900, f"Expected 8900, got {summary['output']}"
        assert summary["requests"] == 5, f"Expected 5, got {summary['requests']}"
        assert summary["cost"] > 0

    def test_api_logs_pagination(self, test_client):
        """Scenario 2e — GET /api/logs paginates correctly."""
        # Small page
        resp = test_client.get("/api/logs?limit=2&page=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 2
        assert data["total"] == 5  # 3 Claude + 2 Hermes
        assert data["page"] == 1

        # Check items have required fields
        for item in data["items"]:
            assert "request_id" in item
            assert "model" in item
            assert "input_tokens" in item
            assert "output_tokens" in item
            assert "timestamp" in item
            assert "cost" in item

    def test_api_models_endpoint(self, test_client):
        """Scenario 2f — GET /api/models returns model list with counts."""
        resp = test_client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "total" in data
        assert data["total"] == 5, f"Expected 5, got {data['total']}"
        model_names = [m["name"] for m in data["models"]]
        assert "deepseek-v4-pro" in model_names
        assert "mimo-v2.5-pro" in model_names

    def test_api_models_source_filter(self, test_client):
        """Scenario 2g — GET /api/models?source=hermes returns only Hermes models."""
        resp = test_client.get("/api/models?source=hermes")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        for m in data["models"]:
            assert m["name"] in {"mimo-v2.5-pro", "deepseek-v4-flash"}

    def test_api_trends_daily_aggregation(self, test_client):
        """Scenario 2h — GET /api/trends returns daily data structure."""
        resp = test_client.get("/api/trends?time=all")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 2  # at least 2 dates (June 20, June 21)
        for day in data:
            assert "date" in day
            assert "requests" in day
            assert "input" in day
            assert "output" in day

    def test_api_providers_aggregation(self, test_client):
        """Scenario 2i — GET /api/providers returns per-provider stats."""
        resp = test_client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        providers = {p["provider"] for p in data}
        # deepseek-v4-pro → deepseek, claude-... → claude, mimo-... → mimo
        assert "deepseek" in providers
        assert "claude" in providers
        assert "mimo" in providers

    def test_api_refresh_resets_cache(self, test_client):
        """Scenario 2j — POST /api/refresh returns ok and cache is reloaded."""
        resp = test_client.post("/api/refresh")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        # After refresh, stats should still work
        resp2 = test_client.get("/api/stats")
        assert resp2.status_code == 200
        assert len(resp2.json()) > 0

    def test_404_on_unknown_route(self, test_client):
        """Scenario 2k — unknown endpoint returns 404."""
        resp = test_client.get("/api/nonexistent")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════
# S3 — Multi-source merge + pricing integration
# ═══════════════════════════════════════════════════════════════════════

class TestMultiSourceAndPricing:
    """Integration: both parsers → merge → pricing → server."""

    def test_merge_claude_and_hermes_records(self, all_records):
        """Scenario 3a — combined list has records from both sources."""
        sources = {r.data_source for r in all_records}
        assert sources == {"claude", "hermes"}

        claude = [r for r in all_records if r.data_source == "claude"]
        hermes = [r for r in all_records if r.data_source == "hermes"]
        assert len(claude) == 3
        assert len(hermes) == 2

    def test_aggregate_both_sources(self, all_records):
        """Scenario 3b — aggregating both sources produces combined stats."""
        from hermes_token_dash.parser_claude import aggregate_by_model_date

        stats = aggregate_by_model_date(all_records)
        # 3 Claude models + 2 Hermes models, 2 dates
        assert len(stats) >= 5

    def test_pricing_fuzzy_match(self):
        """Scenario 3c — get_model_price fuzzy-matches model name variants."""
        # Exact match
        assert get_model_price("deepseek-v4-pro") == (0.55, 0.19)
        # Substring variant
        assert get_model_price("claude-sonnet-4-6-20250526") == (3.00, 15.00)
        # Unknown model → defaults
        assert get_model_price("nonexistent-model") == (0.50, 2.00)

    def test_pricing_put_then_get(self, test_client):
        """Scenario 3d — PUT /api/pricing changes pricing, then GET /api/pricing
        reflects the new values. GET /api/stats cost changes accordingly."""
        # Get original pricing
        resp_before = test_client.get("/api/pricing")
        assert resp_before.status_code == 200

        # Update pricing for deepseek-v4-pro
        new_pricing = [
            {
                "model": "deepseek-v4-pro",
                "input_price": 10.0,
                "output_price": 20.0,
                "cache_read_price": 1.0,
                "cache_write_price": 2.0,
            }
        ]
        resp_update = test_client.put("/api/pricing", json=new_pricing)
        assert resp_update.status_code == 200
        assert resp_update.json()["ok"] is True

        # Verify pricing changed
        resp_after = test_client.get("/api/pricing")
        pricing_map = {
            p["model"]: p["input_price"] for p in resp_after.json()
        }
        assert pricing_map["deepseek-v4-pro"] == 10.0

        # Stats cost should now reflect new pricing
        resp_stats = test_client.get("/api/stats?model=deepseek-v4-pro")
        stats_data = resp_stats.json()
        if stats_data:
            # With new pricing (10/20 per 1M), cost should be much higher
            assert stats_data[0]["cost"] > 0

    def test_pricing_put_multiple_models(self, test_client):
        """Scenario 3e — PUT /api/pricing updates multiple models at once."""
        new_pricing = [
            {"model": "mimo-v2.5", "input_price": 5.0, "output_price": 10.0},
            {"model": "mimo-v2.5-pro", "input_price": 5.0, "output_price": 10.0},
        ]
        resp = test_client.put("/api/pricing", json=new_pricing)
        assert resp.status_code == 200
        assert resp.json()["updated"] == 2

    def test_api_summary_source_filter_hermes_only(self, test_client):
        """Scenario 3f — GET /api/summary?source=hermes returns only Hermes totals."""
        resp = test_client.get("/api/summary?source=hermes")
        assert resp.status_code == 200
        summary = resp.json()
        # Hermes: 2000+800=2800 input, 1000+3200=4200 output, 2 requests
        assert summary["input"] == 2800
        assert summary["output"] == 4200
        assert summary["requests"] == 2

    def test_end_to_end_latency_and_status_fields(self, test_client):
        """Scenario 3g — TokenUsage fields (latency_ms, status_code, first_token_ms)
        survive round-trip through the logs API."""
        resp = test_client.get("/api/logs?limit=50")
        assert resp.status_code == 200
        items = resp.json()["items"]
        for item in items:
            assert "status_code" in item
            assert "latency_ms" in item
            assert "first_token_ms" in item
            # Defaults from TokenUsage dataclass
            assert item["status_code"] == 200
            assert item["latency_ms"] == 0.0
            assert item["first_token_ms"] == 0.0

    def test_hermes_records_have_correct_source(self, hermes_records):
        """Scenario 3h — Hermes-parsed records carry data_source='hermes'."""
        assert len(hermes_records) == 2
        for r in hermes_records:
            assert r.data_source == "hermes"
            assert r.request_id.startswith("hermes:")

    def test_data_source_preserved_in_server_response(self, test_client):
        """Scenario 3i — /api/logs response includes data_source field
        and correctly identifies claude vs hermes records."""
        resp = test_client.get("/api/logs?limit=50")
        assert resp.status_code == 200
        items = resp.json()["items"]
        sources = {item["data_source"] for item in items}
        assert "claude" in sources
        assert "hermes" in sources
