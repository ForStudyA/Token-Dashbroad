"""OpenAI Codex CLI session parser — extracts token usage from rollout JSONL files.

Data source:
  ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl

Each rollout file contains a session with multiple turns. Token usage is
reported via ``event_msg`` entries with ``type: token_count``.
The ``total_token_usage`` field is cumulative across the session, so
we take the **last** (maximum) value per session as the session total.

Model info comes from ``turn_context`` entries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from hermes_token_dash.models import TokenUsage

logger = logging.getLogger(__name__)

CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"


def scan_codex_jsonls() -> list[Path]:
    """Find all rollout-*.jsonl files under ~/.codex/sessions/."""
    if not CODEX_SESSIONS_DIR.is_dir():
        return []
    return sorted(CODEX_SESSIONS_DIR.rglob("rollout-*.jsonl"))


def parse_codex_jsonl(filepath: Path) -> list[TokenUsage]:
    """Parse one Codex rollout JSONL file.

    Strategy:
    - Extract session_meta for session_id and cwd.
    - Extract turn_context for model name.
    - Track all token_count events; use the last ``total_token_usage``
      (cumulative max) as the session's total usage.
    - Return one TokenUsage record per session.

    For multi-turn sessions, we split by turn: each ``task_started`` event
    begins a new turn. We take the *difference* between consecutive
    ``total_token_usage`` snapshots so each turn gets its own incremental
    usage. If there's only one turn, we use total_token_usage directly.
    """
    records: list[TokenUsage] = []

    session_id = ""
    model = "unknown"
    originator = ""
    turns: list[dict] = []  # list of {turn_id, model, token_snapshots: [...]}
    current_turn: dict | None = None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSON line in %s", filepath)
                    continue

                obj_type = obj.get("type")

                # Session metadata
                if obj_type == "session_meta":
                    payload = obj.get("payload", {})
                    session_id = payload.get("session_id", "") or payload.get("id", "")
                    originator = payload.get("originator", "") or payload.get("source", "")

                # Turn context — has model info
                elif obj_type == "turn_context":
                    payload = obj.get("payload", {})
                    turn_model = payload.get("model", "")
                    if turn_model and turn_model != "unknown":
                        model = turn_model
                        # Also update current turn's model (turn_context comes after task_started)
                        if current_turn is not None:
                            current_turn["model"] = turn_model

                # Event messages
                elif obj_type == "event_msg":
                    payload = obj.get("payload", {})
                    event_type = payload.get("type", "")

                    # New turn started
                    if event_type == "task_started":
                        turn_id = payload.get("turn_id", "")
                        current_turn = {
                            "turn_id": turn_id,
                            "model": model,
                            "snapshots": [],
                            "timestamp": obj.get("timestamp", ""),
                        }
                        turns.append(current_turn)

                    # Token count snapshot
                    elif event_type == "token_count":
                        info = payload.get("info", {})
                        total_usage = info.get("total_token_usage", {})
                        if total_usage and current_turn is not None:
                            current_turn["snapshots"].append(total_usage)
                            # Update model if available in rate_limits context
                            # (sometimes model info is elsewhere)

    except (IOError, OSError) as e:
        logger.warning("Failed to read Codex file %s: %s", filepath, e)
        return []

    if not turns:
        return []

    # Compute per-turn usage from cumulative snapshots
    prev_input = 0
    prev_output = 0
    prev_cache_read = 0

    for i, turn in enumerate(turns):
        if not turn["snapshots"]:
            continue

        # Take the last (max) snapshot for this turn
        last = turn["snapshots"][-1]
        total_input = last.get("input_tokens", 0) or 0
        total_output = last.get("output_tokens", 0) or 0
        total_cache_read = last.get("cached_input_tokens", 0) or 0

        # Incremental usage = current cumulative - previous cumulative
        inc_input = max(0, total_input - prev_input)
        inc_output = max(0, total_output - prev_output)
        inc_cache_read = max(0, total_cache_read - prev_cache_read)

        # Parse timestamp
        ts_str = turn.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        # Cache creation: Codex doesn't report this separately
        # reasoning tokens are included in output_tokens
        turn_model = turn.get("model", model) or model

        records.append(TokenUsage(
            request_id=f"codex:{session_id[:16]}:{turn['turn_id'][:8]}" if turn.get("turn_id") else f"codex:{session_id[:16]}:t{i}",
            model=turn_model,
            input_tokens=inc_input,
            output_tokens=inc_output,
            cache_read=inc_cache_read,
            cache_creation=0,
            timestamp=ts,
            data_source="codex",
            agent=originator,
        ))

        prev_input = total_input
        prev_output = total_output
        prev_cache_read = total_cache_read

    return records
