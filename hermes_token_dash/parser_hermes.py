"""Hermes Agent session parser — parses token usage from Hermes SQLite databases.

Data sources:
  - Main DB:   ~/AppData/Local/hermes/state.db
  - Profiles:  ~/AppData/Local/hermes/profiles/*/state.db

The ``sessions`` table in each DB holds per-session token totals:
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  model, started_at, ended_at, estimated_cost_usd
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_token_dash.models import ModelStats, TokenUsage

logger = logging.getLogger(__name__)


def _discover_hermes_dbs() -> list[Path]:
    """Return every Hermes ``state.db`` file — main DB plus each profile's DB."""
    dbs: list[Path] = []

    main_db = Path.home() / "AppData" / "Local" / "hermes" / "state.db"
    if main_db.exists():
        dbs.append(main_db)

    profiles_dir = Path.home() / "AppData" / "Local" / "hermes" / "profiles"
    if profiles_dir.is_dir():
        for profile_dir in sorted(profiles_dir.iterdir()):
            if not profile_dir.is_dir():
                continue
            db_path = profile_dir / "state.db"
            if db_path.exists():
                dbs.append(db_path)

    return dbs


def parse_hermes_sessions() -> list[TokenUsage]:
    """Parse token usage from all Hermes session databases.

    Each session row becomes one ``TokenUsage`` record keyed by the
    session ``id`` field.  Sessions without token data are skipped.

    Returns a flat list — the caller is responsible for aggregation.
    """
    records: list[TokenUsage] = []

    for db_path in _discover_hermes_dbs():
        # Determine profile name from db_path
        if db_path.parent.name == "hermes":
            profile = "default"
        else:
            profile = db_path.parent.name

        try:
            # Use writable connection for WAL checkpoint
            conn = sqlite3.connect(str(db_path), timeout=5)
            conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            conn.close()

            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """SELECT id, model, input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens,
                          started_at, ended_at, source, api_call_count
                   FROM sessions
                   WHERE input_tokens > 0 OR output_tokens > 0
                """
            )
            for row in cur.fetchall():
                session_id = row["id"] or ""
                model = row["model"] or "unknown"
                in_tok = row["input_tokens"] or 0
                out_tok = row["output_tokens"] or 0
                cache_read = row["cache_read_tokens"] or 0
                cache_creation = row["cache_write_tokens"] or 0
                api_calls = row["api_call_count"] or 1

                agent = "hermes:" + (row["source"] or "unknown")

                # Timestamp: prefer ended_at, fall back to started_at
                ts_val = row["ended_at"] or row["started_at"]
                try:
                    ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                except (TypeError, ValueError, OSError):
                    # For active sessions with future/invalid timestamps, use started_at
                    ts_val = row["started_at"]
                    try:
                        ts = datetime.fromtimestamp(ts_val, tz=timezone.utc)
                    except (TypeError, ValueError, OSError):
                        ts = datetime.now(timezone.utc)

                records.append(
                    TokenUsage(
                        request_id=f"hermes:{session_id[:16]}",
                        model=model,
                        input_tokens=in_tok + cache_read,
                        output_tokens=out_tok,
                        cache_read=cache_read,
                        cache_creation=cache_creation,
                        timestamp=ts,
                        data_source="hermes",
                        profile=profile,
                        agent=agent,
                        api_call_count=api_calls,
                    )
                )
            conn.close()
        except (sqlite3.Error, OSError) as e:
            logger.warning("Failed to read Hermes DB %s: %s", db_path, e)

    return records


def aggregate_by_model_date(
    usages: list[TokenUsage],
    time_filter: str = "all",
) -> list[ModelStats]:
    """Aggregate token usage by (model, date) with time filtering.

    Delegates to ``parser_claude.aggregate_by_model_date`` — the
    aggregation logic is identical for both data sources.
    """
    from hermes_token_dash.parser_claude import (
        aggregate_by_model_date as _agg,
    )
    return _agg(usages, time_filter)


def get_available_models(usages: list[TokenUsage]) -> list[str]:
    """Return sorted list of unique model names in *usages*."""
    from hermes_token_dash.parser_claude import get_available_models as _gam
    return _gam(usages)
