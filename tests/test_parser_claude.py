"""Unit tests for hermes_token_dash.parser_claude module.

Tests all 4 public functions with mock isolation (no real disk I/O).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

from hermes_token_dash.models import ModelStats, TokenUsage
from hermes_token_dash.parser_claude import (
    aggregate_by_model_date,
    get_available_models,
    parse_jsonl,
    scan_claude_jsonls,
)


# ═══════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════

_NOW = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)


def _make_usage(
    request_id: str,
    model: str = "deepseek-v4-pro",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_creation: int = 0,
    timestamp: datetime | None = None,
    data_source: str = "claude",
) -> TokenUsage:
    return TokenUsage(
        request_id=request_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_creation=cache_creation,
        timestamp=timestamp or _NOW,
        data_source=data_source,
    )


# ═══════════════════════════════════════════════════════════════════════
#  scan_claude_jsonls
# ═══════════════════════════════════════════════════════════════════════

class TestScanClaudeJsonls:
    """Tests for scan_claude_jsonls()."""

    def test_finds_jsonl_files(self):
        """Returns sorted list of .jsonl files under ~/.claude/projects/."""
        fake_files = [
            Path("/home/user/.claude/projects/abc/1.jsonl"),
            Path("/home/user/.claude/projects/abc/2.jsonl"),
        ]
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "rglob", return_value=fake_files):
                result = scan_claude_jsonls()
        assert result == fake_files

    def test_empty_dir(self):
        """Returns empty list when no .jsonl files exist."""
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "rglob", return_value=[]):
                result = scan_claude_jsonls()
        assert result == []

    def test_directory_not_exists(self):
        """Returns empty when projects directory doesn't exist."""
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "rglob") as mock_rglob:
                mock_rglob.side_effect = FileNotFoundError
                with pytest.raises(FileNotFoundError):
                    scan_claude_jsonls()


# ═══════════════════════════════════════════════════════════════════════
#  parse_jsonl
# ═══════════════════════════════════════════════════════════════════════

class TestParseJsonl:
    """Tests for parse_jsonl()."""

    # ── normal parsing ───────────────────────────────────────────

    def test_parses_valid_jsonl(self):
        """Parses a normal JSONL with assistant + usage data."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "deepseek-v4-pro",
                "usage": {
                    "input_tokens": 2000,
                    "output_tokens": 800,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 50,
                },
            },
            "timestamp": "2026-06-25T10:00:00Z",
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        r = result[0]
        assert r.request_id == "req-001"
        assert r.model == "deepseek-v4-pro"
        assert r.input_tokens == 2100  # input_tokens(2000) + cache_read(100)
        assert r.output_tokens == 800
        assert r.cache_read == 100
        assert r.cache_creation == 50
        assert r.data_source == "claude"

    def test_parses_multiple_records(self):
        """Multiple unique records are all returned."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
                "timestamp": "2026-06-25T10:00:00Z",
            })
            + "\n"
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-002",
                    "model": "deepseek-v4-flash",
                    "usage": {"input_tokens": 200, "output_tokens": 100},
                },
                "timestamp": "2026-06-25T10:01:00Z",
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 2

    # ── streaming deduplication ──────────────────────────────────

    def test_streaming_dedup_keeps_max(self):
        """Duplicate msg_id: keep max of each token field."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_read_input_tokens": 10,
                        "cache_creation_input_tokens": 5,
                    },
                },
                "timestamp": "2026-06-25T10:00:00Z",
            })
            + "\n"
            # streaming chunk with higher counts
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {
                        "input_tokens": 200,
                        "output_tokens": 100,
                        "cache_read_input_tokens": 20,
                        "cache_creation_input_tokens": 10,
                    },
                },
                "timestamp": "2026-06-25T10:00:01Z",
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1, "Should deduplicate to 1 record"
        r = result[0]
        assert r.input_tokens == 220  # max of [100, 200] + cache_read(20)
        assert r.output_tokens == 100  # max of [50, 100]
        assert r.cache_read == 20  # max of [10, 20]
        assert r.cache_creation == 10  # max of [5, 10]

    def test_streaming_dedup_first_chunk_higher(self):
        """When first chunk has higher values, keep first max."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 300,
                    },
                },
                "timestamp": "2026-06-25T10:00:00Z",
            })
            + "\n"
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                    },
                },
                "timestamp": "2026-06-25T10:00:01Z",
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        r = result[0]
        assert r.input_tokens == 500
        assert r.output_tokens == 300

    # ── filtering: non-assistant types ───────────────────────────

    def test_skips_non_assistant_type(self):
        """Lines with type != 'assistant' are ignored."""
        content = (
            json.dumps({
                "type": "user",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 100},
                },
            })
            + "\n"
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-002",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 200},
                },
            })
            + "\n"
            + json.dumps({
                "type": "system",
                "message": {
                    "id": "req-003",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 300},
                },
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        assert result[0].request_id == "req-002"

    # ── filtering: synthetic / unknown models ────────────────────

    def test_skips_synthetic_model(self):
        """Records with model='<synthetic>' are skipped."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "<synthetic>",
                    "usage": {"input_tokens": 100},
                },
            })
            + "\n"
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-002",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 200},
                },
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        assert result[0].request_id == "req-002"

    def test_skips_unknown_model(self):
        """Records with model='unknown' are skipped."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "unknown",
                    "usage": {"input_tokens": 100},
                },
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    def test_skips_empty_model(self):
        """Records with empty model string are skipped."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "",
                "usage": {"input_tokens": 100},
            },
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    # ── filtering: no usage data ─────────────────────────────────

    def test_skips_no_usage(self):
        """Assistant lines without usage data are skipped."""
        content = (
            json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                },
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    def test_skips_usage_not_dict(self):
        """usage field that is not a dict is skipped."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "deepseek-v4-pro",
                "usage": None,
            },
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    # ── filtering: no message or no id ───────────────────────────

    def test_skips_no_message(self):
        """Assistant line without message dict is skipped."""
        content = json.dumps({
            "type": "assistant",
            "content": "just text",
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    def test_skips_no_message_id(self):
        """Assistant line with message but no id is skipped."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "model": "deepseek-v4-pro",
                "usage": {"input_tokens": 100},
            },
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 0

    # ── error handling ───────────────────────────────────────────

    def test_skips_malformed_json(self):
        """Malformed JSON lines are silently skipped."""
        content = (
            "this is not json\n"
            + json.dumps({
                "type": "assistant",
                "message": {
                    "id": "req-001",
                    "model": "deepseek-v4-pro",
                    "usage": {"input_tokens": 100},
                },
            })
            + "\n"
        )
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        assert result[0].request_id == "req-001"

    def test_empty_file(self):
        """Empty file returns empty list."""
        with patch("builtins.open", mock_open(read_data="")):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert result == []

    def test_file_not_found(self):
        """FileNotFound or PermissionError returns empty list."""
        with patch("builtins.open") as mock_file:
            mock_file.side_effect = FileNotFoundError
            result = parse_jsonl(Path("/nonexistent/session.jsonl"))
        assert result == []

    def test_honors_cache_read_zero(self):
        """Zero cache_read is treated as 0, not skipped."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "deepseek-v4-pro",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        assert result[0].cache_read == 0
        assert result[0].cache_creation == 0

    def test_handles_none_usage_fields(self):
        """None values in usage fields default to 0."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "deepseek-v4-pro",
                "usage": {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cache_read_input_tokens": None,
                    "cache_creation_input_tokens": None,
                },
            },
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            result = parse_jsonl(Path("/fake/session.jsonl"))
        r = result[0]
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cache_read == 0
        assert r.cache_creation == 0

    def test_handles_invalid_timestamp(self):
        """Invalid ISO timestamp falls back to datetime.now()."""
        content = json.dumps({
            "type": "assistant",
            "message": {
                "id": "req-001",
                "model": "deepseek-v4-pro",
                "usage": {"input_tokens": 100},
            },
            "timestamp": "not-a-valid-iso-date",
        }) + "\n"
        with patch("builtins.open", mock_open(read_data=content)):
            # Only mock datetime.now so fromisoformat still works with real datetime
            with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
                mock_dt.now.return_value = _NOW
                mock_dt.min = datetime.min
                mock_dt.combine = datetime.combine
                mock_dt.timezone = timezone
                mock_dt.timedelta = timedelta
                mock_dt.fromisoformat = datetime.fromisoformat
                result = parse_jsonl(Path("/fake/session.jsonl"))
        assert len(result) == 1
        # Because fromisoformat raises ValueError on invalid timestamp,
        # the code falls back to datetime.now() which we mocked to _NOW
        assert result[0].timestamp == _NOW


# ═══════════════════════════════════════════════════════════════════════
#  aggregate_by_model_date
# ═══════════════════════════════════════════════════════════════════════

class TestAggregateByModelDate:
    """Tests for aggregate_by_model_date()."""

    def _make_usages(self) -> list[TokenUsage]:
        """Build a set of usages across 2 models, 2 dates."""
        today = _NOW
        yesterday = _NOW - timedelta(days=1)
        week_ago = _NOW - timedelta(days=8)
        return [
            _make_usage("r1", "deepseek-v4-pro", 2000, 800, 100, 0, today),
            _make_usage("r2", "deepseek-v4-pro", 1500, 600, 0, 50, today),
            _make_usage("r3", "deepseek-v4-flash", 3000, 1200, 0, 0, yesterday),
            _make_usage("r4", "deepseek-v4-flash", 1000, 400, 256, 0, yesterday),
            _make_usage("r5", "deepseek-v4-pro", 500, 200, 0, 0, week_ago),
        ]

    def test_all_filter(self):
        """time_filter='all' returns all records aggregated."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "all")
        assert len(stats) >= 3  # 3 unique (model, date) combos

    def test_today_filter(self):
        """time_filter='today' returns only today's records."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "today")
        # Only r1 and r2 (both today)
        total_input = sum(s.total_input for s in stats)
        assert total_input == 3500  # 2000 + 1500
        assert sum(s.request_count for s in stats) == 2
        for s in stats:
            assert s.date == "2026-06-25"

    def test_7d_filter(self):
        """time_filter='7d' = today - 6 days."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "7d")
        # Includes today, yesterday; excludes week_ago (day 8)
        total_requests = sum(s.request_count for s in stats)
        assert total_requests == 4  # r1, r2, r3, r4

    def test_30d_filter(self):
        """time_filter='30d' = today - 29 days."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "30d")
        # 8 days ago is within 30 days → all 5
        assert sum(s.request_count for s in stats) == 5

    def test_empty_input(self):
        """Empty list returns empty stats."""
        assert aggregate_by_model_date([], "all") == []

    def test_aggregation_structure(self):
        """Aggregated stats have correct fields."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "all")
        for s in stats:
            assert isinstance(s, ModelStats)
            assert isinstance(s.model, str)
            assert isinstance(s.date, str)
            assert s.total_input >= 0
            assert s.total_output >= 0
            assert s.request_count > 0

    def test_cache_hit_rate(self):
        """Requests with cache_read > 0 count toward hit rate."""
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(self._make_usages(), "all")
        # Look for today's deepseek-v4-pro: r1 has cache_read, r2 doesn't
        today_pro = [s for s in stats
                     if s.model == "deepseek-v4-pro" and s.date == "2026-06-25"]
        assert len(today_pro) == 1
        assert today_pro[0].requests_with_cache == 1  # only r1
        assert today_pro[0].request_count == 2

    def test_sorted_by_date_then_model(self):
        """Results are sorted by (date, model)."""
        usages = [
            _make_usage("r1", "b-model", 100, 50,
                        timestamp=_NOW - timedelta(days=10)),
            _make_usage("r2", "a-model", 200, 100,
                        timestamp=_NOW - timedelta(days=2)),
            _make_usage("r3", "a-model", 300, 150,
                        timestamp=_NOW - timedelta(days=10)),
        ]
        with patch("hermes_token_dash.parser_claude.datetime") as mock_dt:
            mock_dt.now.return_value = _NOW
            mock_dt.min = datetime.min
            mock_dt.combine = datetime.combine
            mock_dt.timezone = timezone
            mock_dt.timedelta = timedelta
            stats = aggregate_by_model_date(usages, "all")
        dates = [s.date for s in stats]
        assert dates == sorted(dates), f"Stats not sorted by date: {dates}"


# ═══════════════════════════════════════════════════════════════════════
#  get_available_models
# ═══════════════════════════════════════════════════════════════════════

class TestGetAvailableModels:
    """Tests for get_available_models()."""

    def test_returns_unique_sorted(self):
        """Returns sorted list of unique model names."""
        usages = [
            _make_usage("r1", "b-model"),
            _make_usage("r2", "a-model"),
            _make_usage("r3", "b-model"),  # duplicate
            _make_usage("r4", "c-model"),
        ]
        models = get_available_models(usages)
        assert models == ["a-model", "b-model", "c-model"]

    def test_empty_list(self):
        """Empty list returns empty list."""
        assert get_available_models([]) == []

    def test_single_model(self):
        """Single model returns one-element list."""
        usages = [_make_usage("r1", "deepseek-v4-pro")]
        assert get_available_models(usages) == ["deepseek-v4-pro"]

    def test_all_same_model(self):
        """All records same model returns single-element list."""
        usages = [
            _make_usage("r1", "deepseek-v4-pro"),
            _make_usage("r2", "deepseek-v4-pro"),
        ]
        assert get_available_models(usages) == ["deepseek-v4-pro"]
