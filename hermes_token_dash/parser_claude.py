"""Claude Code JSONL parser — parses token usage from ~/.claude/projects/."""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hermes_token_dash.models import ModelStats, TokenUsage

logger = logging.getLogger(__name__)


def scan_claude_jsonls() -> list[Path]:
    """Find all *.jsonl files under ``~/.claude/projects/``."""
    projects_dir = Path.home() / ".claude" / "projects"
    return sorted(projects_dir.rglob("*.jsonl"))


def parse_jsonl(filepath: Path) -> list[TokenUsage]:
    """Parse one Claude Code JSONL file.

    Extracts ``assistant``-type lines with ``message.usage`` data and
    deduplicates by ``message.id`` (the requestId).  Streaming duplicates
    (multiple lines with the same *message.id*) are resolved by taking the
    **maximum** of each usage field across chunks.
    """
    seen: dict[str, TokenUsage] = {}

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

                if obj.get("type") != "assistant":
                    continue

                message = obj.get("message")
                if not message or not isinstance(message, dict):
                    continue

                msg_id = message.get("id")
                if not msg_id:
                    continue

                model = message.get("model", "unknown")
                # Skip synthetic/placeholder records (internal Claude Code bookkeeping)
                if model == "<synthetic>" or not model or model == "unknown":
                    continue
                usage = message.get("usage")
                if not usage or not isinstance(usage, dict):
                    continue

                input_tokens = usage.get("input_tokens", 0) or 0
                output_tokens = usage.get("output_tokens", 0) or 0
                cache_read = usage.get("cache_read_input_tokens", 0) or 0
                cache_creation = usage.get("cache_creation_input_tokens", 0) or 0

                # Parse ISO-8601 timestamp
                timestamp_str = obj.get("timestamp", "")
                try:
                    ts = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    ts = datetime.now(timezone.utc)

                entrypoint = obj.get("entrypoint", "") or ""

                if msg_id in seen:
                    # Streaming duplicate: keep highest token counts, earliest ts
                    existing = seen[msg_id]
                    existing.input_tokens = max(existing.input_tokens, input_tokens + cache_read)
                    existing.output_tokens = max(existing.output_tokens, output_tokens)
                    existing.cache_read = max(existing.cache_read, cache_read)
                    existing.cache_creation = max(
                        existing.cache_creation, cache_creation
                    )
                else:
                    seen[msg_id] = TokenUsage(
                        request_id=msg_id,
                        model=model,
                        input_tokens=input_tokens + cache_read,
                        output_tokens=output_tokens,
                        cache_read=cache_read,
                        cache_creation=cache_creation,
                        timestamp=ts,
                        data_source="claude",
                        agent=entrypoint,
                    )
    except (IOError, OSError) as e:
        logger.warning("Failed to read %s: %s", filepath, e)

    return list(seen.values())


def get_time_cutoff(time_filter: str) -> datetime:
    """Return a UTC ``datetime`` cutoff for the given *time_filter*.

    *time_filter* options: ``"all"`` | ``"today"`` | ``"7d"`` | ``"30d"``
    Returns ``datetime.min`` with UTC tzinfo for ``"all"`` so that every
    record passes the filter.
    """
    now = datetime.now(timezone.utc)
    today = now.date()

    if time_filter == "today":
        return datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    elif time_filter == "7d":
        return datetime.combine(
            today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc
        )
    elif time_filter == "30d":
        return datetime.combine(
            today - timedelta(days=29), datetime.min.time(), tzinfo=timezone.utc
        )
    else:  # "all"
        return datetime.min.replace(tzinfo=timezone.utc)


def aggregate_by_model_date(
    usages: list[TokenUsage],
    time_filter: str = "all",
) -> list[ModelStats]:
    """Aggregate token usage by ``(model, date)`` with optional time filtering.

    *time_filter* options: ``"all"`` | ``"today"`` | ``"7d"`` | ``"30d"``
    """
    cutoff = get_time_cutoff(time_filter)
    filtered = [r for r in usages if r.timestamp >= cutoff]

    # Aggregate by (model, date)
    agg: defaultdict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "total_input": 0,
            "total_output": 0,
            "total_cache_read": 0,
            "total_cache_creation": 0,
            "request_count": 0,
            "requests_with_cache": 0,
        }
    )

    for rec in filtered:
        key = (rec.model, rec.timestamp.date().isoformat())
        d = agg[key]
        d["total_input"] += rec.input_tokens
        d["total_output"] += rec.output_tokens
        d["total_cache_read"] += rec.cache_read
        d["total_cache_creation"] += rec.cache_creation
        d["request_count"] += rec.api_call_count  # 使用实际API调用次数
        if rec.cache_read > 0:
            d["requests_with_cache"] += 1

    result = []
    for (model_name, date_str), d in agg.items():
        s = ModelStats(
            model=model_name,
            date=date_str,
            total_input=d["total_input"],
            total_output=d["total_output"],
            total_cache_read=d["total_cache_read"],
            total_cache_creation=d["total_cache_creation"],
            request_count=d["request_count"],
            requests_with_cache=d["requests_with_cache"],
        )
        s.compute_derived()
        result.append(s)

    result.sort(key=lambda s: (s.date, s.model))
    return result


def get_available_models(usages: list[TokenUsage]) -> list[str]:
    """Return sorted list of unique model names present in *usages*."""
    return sorted({r.model for r in usages})
