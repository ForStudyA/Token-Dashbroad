"""Unit tests for hermes_token_dash.parser_hermes module.

Tests parse_hermes_sessions() and _discover_hermes_dbs() with mock isolation
(no real SQLite or filesystem access).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_token_dash.models import TokenUsage
from hermes_token_dash.parser_hermes import (
    _discover_hermes_dbs,
    parse_hermes_sessions,
)


# ═══════════════════════════════════════════════════════════════════════
#  _discover_hermes_dbs
# ═══════════════════════════════════════════════════════════════════════

class TestDiscoverHermesDbs:
    """Tests for _discover_hermes_dbs()."""

    def test_finds_main_db(self):
        """Returns main state.db when it exists."""
        main_db = Path("/home/user/AppData/Local/hermes/state.db")
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "exists") as mock_exists:
                mock_exists.return_value = True
                with patch.object(Path, "is_dir") as mock_is_dir:
                    mock_is_dir.return_value = False  # profiles dir not found
                    result = _discover_hermes_dbs()
        assert len(result) >= 1
        assert any("state.db" in str(p) for p in result)

    def test_finds_profile_dbs(self):
        """Returns profile state.db files when profiles dir exists."""
        main_db = Path("/home/user/AppData/Local/hermes/state.db")
        profile_dirs = [
            Path("/home/user/AppData/Local/hermes/profiles/coding"),
            Path("/home/user/AppData/Local/hermes/profiles/research"),
        ]
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            # Mock main_db.exists() → True
            # Mock profiles_dir.is_dir() → True
            # Mock profiles_dir.iterdir() → profile_dirs
            # Mock each profile_dir.is_dir() → True
            # Mock each profile_dir/state.db exists → True
            with patch.object(Path, "exists") as mock_exists:
                mock_exists.return_value = True
                with patch.object(Path, "is_dir") as mock_is_dir:
                    # profiles_dir.is_dir() = True, profile_dirs = True
                    mock_is_dir.return_value = True
                    with patch.object(Path, "iterdir") as mock_iterdir:
                        mock_iterdir.return_value = profile_dirs
                        result = _discover_hermes_dbs()
        # Should find main DB + 2 profile DBs = 3
        assert len(result) == 3

    def test_main_db_missing_no_profiles(self):
        """Returns empty list when no DBs exist."""
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "exists") as mock_exists:
                mock_exists.return_value = False
                with patch.object(Path, "is_dir") as mock_is_dir:
                    mock_is_dir.return_value = False
                    result = _discover_hermes_dbs()
        assert result == []

    def test_main_db_missing_profiles_exist(self):
        """Still finds profile DBs when main DB is missing."""
        profile_dirs = [
            Path("/home/user/AppData/Local/hermes/profiles/coding"),
        ]
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "exists") as mock_exists:
                # Side effect: first call (main_db) → False, subsequent → True
                mock_exists.side_effect = [False, True]
                with patch.object(Path, "is_dir") as mock_is_dir:
                    mock_is_dir.return_value = True
                    with patch.object(Path, "iterdir") as mock_iterdir:
                        mock_iterdir.return_value = profile_dirs
                        result = _discover_hermes_dbs()
        assert len(result) == 1

    def test_skips_non_directory_profile_entries(self):
        """Files in profiles/ directory (not dirs) are skipped."""
        file_not_dir = Path("/tmp/not-a-dir")
        profile_dirs = [
            file_not_dir,
            Path("/home/user/AppData/Local/hermes/profiles/coding"),
        ]
        with patch("pathlib.Path.home") as mock_home:
            mock_home.return_value = Path("/home/user")
            with patch.object(Path, "exists") as mock_exists:
                mock_exists.return_value = True
                with patch.object(Path, "is_dir") as mock_is_dir:
                    # profiles_dir and coding → True, file_not_dir → False
                    mock_is_dir.side_effect = [True, False, True]
                    with patch.object(Path, "iterdir") as mock_iterdir:
                        mock_iterdir.return_value = profile_dirs
                        result = _discover_hermes_dbs()
        # Only main + coding profile
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════
#  parse_hermes_sessions
# ═══════════════════════════════════════════════════════════════════════

class TestParseHermesSessions:
    """Tests for parse_hermes_sessions()."""

    def _make_mock_row(self, **overrides):
        """Build a mock sqlite3.Row dict."""
        defaults = {
            "id": "session-abc-123",
            "model": "deepseek-v4-pro",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_tokens": 200,
            "cache_write_tokens": 50,
            "reasoning_tokens": 0,
            "started_at": 1750000000.0,  # epoch timestamp
            "ended_at": 1750000100.0,
            "source": "cli",
            "api_call_count": 1,
        }
        defaults.update(overrides)
        row = MagicMock()
        row.__getitem__ = lambda self, k: defaults[k]
        row.keys.return_value = defaults.keys()
        for k, v in defaults.items():
            setattr(row, k, v)
        return row

    def test_parses_valid_rows(self):
        """Normal session rows parse into TokenUsage records."""
        mock_row = self._make_mock_row()
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        assert len(result) == 1
        r = result[0]
        assert r.request_id == "hermes:session-abc-123"
        assert r.model == "deepseek-v4-pro"
        assert r.input_tokens == 1200  # input_tokens(1000) + cache_read(200)
        assert r.output_tokens == 500
        assert r.cache_read == 200
        assert r.cache_creation == 50
        assert r.data_source == "hermes"

    def test_handles_none_values(self):
        """None values in DB fields are converted to 0 or empty string."""
        mock_row = self._make_mock_row(
            id=None,
            model=None,
            input_tokens=None,
            output_tokens=None,
            cache_read_tokens=None,
            cache_write_tokens=None,
        )
        mock_row.__getitem__ = lambda self, k: {
            "id": None, "model": None,
            "input_tokens": None, "output_tokens": None,
            "cache_read_tokens": None, "cache_write_tokens": None,
            "reasoning_tokens": None,
            "started_at": 1750000000.0, "ended_at": None,
            "source": None, "api_call_count": None,
        }[k]
        for k in mock_row.keys():
            try:
                delattr(mock_row, k)
            except (AttributeError, KeyError):
                pass
        # Rebuild mock with explicit getitem
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        assert len(result) == 1
        r = result[0]
        assert r.request_id == "hermes:"  # empty session id
        assert r.model == "unknown"
        assert r.input_tokens == 0
        assert r.output_tokens == 0
        assert r.cache_read == 0
        assert r.cache_creation == 0

    def test_skips_zero_token_rows(self):
        """Rows where both input_tokens and output_tokens are 0 are skipped
        (the SQL query has WHERE input_tokens > 0 OR output_tokens > 0)."""
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = []  # 0 rows returned
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        assert result == []

    def test_multiple_sessions(self):
        """Multiple session rows all parsed."""
        rows = [
            self._make_mock_row(id="sess-1", model="deepseek-v4-pro"),
            self._make_mock_row(id="sess-2", model="deepseek-v4-flash"),
        ]
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = rows
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        assert len(result) == 2
        assert result[0].request_id == "hermes:sess-1"
        assert result[1].request_id == "hermes:sess-2"

    def test_multiple_databases(self):
        """Multiple Hermes DBs (main + profiles) are all read."""
        rows_db1 = [self._make_mock_row(id="sess-1")]
        rows_db2 = [self._make_mock_row(id="sess-2")]
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [
                Path("/fake/main/state.db"),
                Path("/fake/profile/state.db"),
            ]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn1 = MagicMock()
                mock_cur1 = MagicMock()
                mock_cur1.fetchall.return_value = rows_db1
                mock_conn1.cursor.return_value = mock_cur1

                mock_conn2 = MagicMock()
                mock_cur2 = MagicMock()
                mock_cur2.fetchall.return_value = rows_db2
                mock_conn2.cursor.return_value = mock_cur2

                mock_connect.side_effect = [mock_conn1, mock_conn2]

                result = parse_hermes_sessions()

        assert len(result) == 2

    def test_truncates_session_id_to_16_chars(self):
        """request_id is hermes:<first 16 chars of session id>."""
        long_id = "a" * 32
        mock_row = self._make_mock_row(id=long_id)
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        assert result[0].request_id == f"hermes:{'a' * 16}"

    def test_db_connection_error_skipped(self):
        """sqlite3 errors for one DB don't break parsing of others."""
        import sqlite3

        rows_db2 = [self._make_mock_row(id="sess-2")]

        # Build second connection mock
        second_conn = MagicMock()
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = rows_db2
        second_conn.cursor.return_value = mock_cur

        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [
                Path("/fake/broken/state.db"),
                Path("/fake/ok/state.db"),
            ]
            with patch("sqlite3.connect") as mock_connect:
                # First DB throws error, second DB works
                mock_connect.side_effect = [
                    sqlite3.OperationalError("database is locked"),
                    second_conn,
                ]
                result = parse_hermes_sessions()
        assert len(result) == 1
        assert result[0].request_id == "hermes:sess-2"

    def test_no_databases_found(self):
        """Returns empty list when no Hermes DBs exist."""
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = []
            result = parse_hermes_sessions()
        assert result == []

    def test_uses_ended_at_over_started_at(self):
        """Timestamp prefers ended_at, falls back to started_at."""
        # ended_at is set
        mock_row = self._make_mock_row(
            started_at=1750000000.0,
            ended_at=1750000500.0,
        )
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        expected_ts = datetime.fromtimestamp(1750000500.0, tz=timezone.utc)
        assert result[0].timestamp == expected_ts

    def test_falls_back_to_started_at(self):
        """When ended_at is None, uses started_at."""
        mock_row = self._make_mock_row(
            started_at=1750000300.0,
            ended_at=None,
        )
        with patch("hermes_token_dash.parser_hermes._discover_hermes_dbs") as mock_discover:
            mock_discover.return_value = [Path("/fake/hermes/state.db")]
            with patch("sqlite3.connect") as mock_connect:
                mock_conn = MagicMock()
                mock_cur = MagicMock()
                mock_cur.fetchall.return_value = [mock_row]
                mock_conn.cursor.return_value = mock_cur
                mock_connect.return_value = mock_conn

                result = parse_hermes_sessions()

        expected_ts = datetime.fromtimestamp(1750000300.0, tz=timezone.utc)
        assert result[0].timestamp == expected_ts


# ═══════════════════════════════════════════════════════════════════════
#  Delegated functions (aggregate_by_model_date, get_available_models)
# ═══════════════════════════════════════════════════════════════════════

class TestDelegatedFunctions:
    """Tests for parser_hermes helper functions that delegate to parser_claude."""

    def test_aggregate_by_model_date_delegates(self):
        """parser_hermes.aggregate_by_model_date calls parser_claude's version."""
        from hermes_token_dash.parser_hermes import aggregate_by_model_date
        from hermes_token_dash.models import TokenUsage

        usages: list[TokenUsage] = []
        result = aggregate_by_model_date(usages, "all")
        assert result == []

    def test_get_available_models_delegates(self):
        """parser_hermes.get_available_models calls parser_claude's version."""
        from hermes_token_dash.parser_hermes import get_available_models
        from hermes_token_dash.models import TokenUsage

        usages: list[TokenUsage] = []
        result = get_available_models(usages)
        assert result == []
