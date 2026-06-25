"""Shared fixtures for hermes-token-dash integration tests."""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ── Test data: Claude Code JSONL records ────────────────────────────────
CLAUDE_RECORDS = [
    {
        "type": "assistant",
        "message": {
            "id": "req-001",
            "model": "deepseek-v4-pro",
            "usage": {
                "input_tokens": 1500,
                "output_tokens": 800,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 0,
            },
        },
        "timestamp": "2026-06-20T10:00:00Z",
    },
    {
        "type": "assistant",
        "message": {
            "id": "req-002",
            "model": "claude-sonnet-4-6-20250526",
            "usage": {
                "input_tokens": 3000,
                "output_tokens": 1200,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 500,
            },
        },
        "timestamp": "2026-06-20T14:00:00Z",
    },
    {
        "type": "assistant",
        "message": {
            "id": "req-003",
            "model": "mimo-v2.5",
            "usage": {
                "input_tokens": 500,
                "output_tokens": 2500,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
        "timestamp": "2026-06-21T09:00:00Z",
    },
    # Streaming duplicate for req-001 (higher token counts should win)
    {
        "type": "assistant",
        "message": {
            "id": "req-001",
            "model": "deepseek-v4-pro",
            "usage": {
                "input_tokens": 2000,
                "output_tokens": 1000,
                "cache_read_input_tokens": 300,
                "cache_creation_input_tokens": 0,
            },
        },
        "timestamp": "2026-06-20T10:00:01Z",
    },
    # Malformed line (should be skipped)
    '{"type": "assistant"}',
    # Non-assistant type (should be skipped)
    {
        "type": "user",
        "message": {
            "id": "req-999",
            "model": "deepseek-v4-pro",
            "usage": {"input_tokens": 9999, "output_tokens": 9999},
        },
        "timestamp": "2026-06-20T12:00:00Z",
    },
    # Synthetic model (should be skipped)
    {
        "type": "assistant",
        "message": {
            "id": "req-synth",
            "model": "<synthetic>",
            "usage": {"input_tokens": 100, "output_tokens": 100},
        },
        "timestamp": "2026-06-20T11:00:00Z",
    },
]

# ── Test data: Hermes session DB rows ───────────────────────────────────
HERMES_SESSIONS = [
    (
        "session-a",
        "mimo-v2.5-pro",
        2000,
        1000,
        0,
        0,
        # 2026-06-20T08:00:00Z
        int(datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 6, 20, 8, 5, 0, tzinfo=timezone.utc).timestamp()),
    ),
    (
        "session-b",
        "deepseek-v4-flash",
        800,
        3200,
        100,
        0,
        int(datetime(2026, 6, 21, 15, 0, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 6, 21, 15, 10, 0, tzinfo=timezone.utc).timestamp()),
    ),
    # Session with zero tokens (should be skipped by query WHERE clause)
    (
        "session-empty",
        "unknown",
        0,
        0,
        0,
        0,
        int(datetime(2026, 6, 19, 0, 0, 0, tzinfo=timezone.utc).timestamp()),
        int(datetime(2026, 6, 19, 0, 1, 0, tzinfo=timezone.utc).timestamp()),
    ),
]


@pytest.fixture
def temp_jsonl_file():
    """Create a temporary JSONL file with known Claude Code records."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        for rec in CLAUDE_RECORDS:
            if isinstance(rec, str):
                f.write(rec + "\n")
            else:
                f.write(json.dumps(rec) + "\n")
    yield Path(f.name)
    os.unlink(f.name)


@pytest.fixture
def temp_hermes_db():
    """Create a temporary SQLite DB mimicking a Hermes state.db."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            started_at REAL,
            ended_at REAL
        )
    """)
    conn.executemany(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        HERMES_SESSIONS,
    )
    conn.commit()
    conn.close()

    yield Path(path)
    os.unlink(path)


@pytest.fixture
def claude_records(temp_jsonl_file):
    """Parse the temp JSONL file and return TokenUsage records."""
    from hermes_token_dash.parser_claude import parse_jsonl

    return parse_jsonl(temp_jsonl_file)


@pytest.fixture
def hermes_records(temp_hermes_db):
    """Parse the temp Hermes DB and return TokenUsage records."""
    from hermes_token_dash.parser_hermes import parse_hermes_sessions
    from hermes_token_dash.parser_hermes import _discover_hermes_dbs

    with patch(
        "hermes_token_dash.parser_hermes._discover_hermes_dbs",
        return_value=[temp_hermes_db],
    ):
        return parse_hermes_sessions()


@pytest.fixture
def all_records(claude_records, hermes_records):
    """Combined Claude + Hermes records."""
    return claude_records + hermes_records


@pytest.fixture
def test_client(temp_jsonl_file, temp_hermes_db):
    """FastAPI TestClient with _get_records patched to return only test data.

    We patch ``_get_records`` directly instead of the lower-level parser
    functions because the server caches parsed data in a module-level
    ``_cache`` list.  Patching ``_get_records`` ensures every endpoint
    sees ONLY the test data, never real disk data."""
    from hermes_token_dash import parser_claude, parser_hermes, server

    # Clear the module-level cache and force re-parse with test-only sources
    server._cache = []

    # Parse test data once with patched DB discovery
    with patch.object(parser_hermes, "_discover_hermes_dbs",
                      return_value=[temp_hermes_db]):
        hermes = parser_hermes.parse_hermes_sessions()

    claude = parser_claude.parse_jsonl(temp_jsonl_file)
    test_records = claude + hermes

    with patch.object(server, "_get_records", return_value=test_records):
        from fastapi.testclient import TestClient
        client = TestClient(server.app)
        yield client
