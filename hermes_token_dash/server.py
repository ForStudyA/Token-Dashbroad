"""Hermes Token Dashboard — FastAPI server.

Serves token usage data via REST API and the Vue 3 frontend.
"""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hermes_token_dash import models
from hermes_token_dash.models import (
    MODEL_PRICING,
    extract_provider,
    get_model_price,
)
from hermes_token_dash.parser_claude import (
    aggregate_by_model_date,
    get_available_models,
    get_time_cutoff,
    parse_jsonl,
    scan_claude_jsonls,
)
from hermes_token_dash.parser_codex import parse_codex_jsonl, scan_codex_jsonls
from hermes_token_dash.parser_hermes import parse_hermes_sessions



def _get_user_input_counts(
    tz_offset: int = 8,
    time_filter: str = "all",
    start: str = "",
    end: str = "",
    source: str = "",
    profile: str = "",
    agent: str = "",
    model: str = "",
) -> dict[str, int]:
    """Get filtered user-message counts per Hermes model."""
    import sqlite3
    from pathlib import Path
    from datetime import datetime, timedelta, timezone as tz

    if source and source != "hermes":
        return {}

    counts: dict[str, int] = {}
    dbs = []
    main_db = Path.home() / "AppData" / "Local" / "hermes" / "state.db"
    if main_db.exists():
        dbs.append(main_db)
    profiles_dir = Path.home() / "AppData" / "Local" / "hermes" / "profiles"
    if profiles_dir.is_dir():
        for profile_dir in profiles_dir.iterdir():
            if profile_dir.is_dir():
                db_path = profile_dir / "state.db"
                if db_path.exists():
                    dbs.append(db_path)

    user_tz = tz(timedelta(hours=tz_offset))
    for db_path in dbs:
        db_profile = "default" if db_path.parent.name == "hermes" else db_path.parent.name
        if profile and db_profile != profile:
            continue

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            conditions = []
            params: list = []

            if time_filter == "custom":
                if start:
                    try:
                        s = datetime.fromisoformat(start)
                        if s.tzinfo is None:
                            s = s.replace(tzinfo=user_tz)
                        conditions.append("s.started_at >= ?")
                        params.append(s.timestamp())
                    except (ValueError, TypeError):
                        pass
                if end:
                    try:
                        e = datetime.fromisoformat(end)
                        if e.tzinfo is None:
                            e = e.replace(tzinfo=user_tz)
                        conditions.append("s.started_at <= ?")
                        params.append(e.timestamp())
                    except (ValueError, TypeError):
                        pass
            elif time_filter != "all":
                from hermes_token_dash.parser_claude import get_time_cutoff

                cutoff = get_time_cutoff(time_filter, tz_offset)
                conditions.append("s.started_at >= ?")
                params.append(cutoff.timestamp())

            if model:
                conditions.append("s.model = ?")
                params.append(model)
            if agent:
                conditions.append("COALESCE(s.source, 'unknown') = ?")
                params.append(agent.removeprefix("hermes:"))

            extra_conditions = "".join(f" AND {condition}" for condition in conditions)
            cur.execute(f"""
                SELECT s.model, COUNT(*) as cnt
                FROM messages m
                JOIN sessions s ON m.session_id = s.id
                WHERE m.role = 'user' AND s.model IS NOT NULL {extra_conditions}
                GROUP BY s.model
            """, params)
            for row in cur.fetchall():
                counts[row[0]] = counts.get(row[0], 0) + row[1]
            conn.close()
        except Exception:
            pass

    return counts


app = FastAPI(title="Hermes Token Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8765", "http://localhost:8765"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC = Path(__file__).parent / "static"
STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# Cache parsed data with TTL (auto-refresh in background after expiry)
import threading

_cache: list = []
_cache_time: float = 0.0
CACHE_TTL: float = 300.0  # 5 minutes
_cache_lock = threading.Lock()
_bg_refreshing = False


@app.on_event("startup")
async def _preload():
    """Pre-load data on startup so first request is instant."""
    import asyncio
    await asyncio.to_thread(_load_cache)


# Incremental file cache: {filepath: (mtime, size, records)}
_file_cache: dict[str, tuple[float, int, list]] = {}


def _load_cache() -> list:
    """Scan and parse all JSONL files + Hermes session DBs + Codex sessions.

    Uses incremental file-level caching: only re-parses files whose
    mtime or size changed since last load.  Unchanged files reuse their
    cached records.
    """
    global _cache, _cache_time
    import os, time

    records: list = []
    seen_files: set[str] = set()

    # Claude Code
    for f in scan_claude_jsonls():
        key = str(f)
        seen_files.add(key)
        try:
            st = os.stat(f)
            cached = _file_cache.get(key)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                records.extend(cached[2])
            else:
                recs = parse_jsonl(f)
                _file_cache[key] = (st.st_mtime, st.st_size, recs)
                records.extend(recs)
        except OSError:
            records.extend(parse_jsonl(f))

    # Hermes Agent (no file-level cache — reads DBs directly)
    records.extend(parse_hermes_sessions())

    # Codex CLI
    for f in scan_codex_jsonls():
        key = str(f)
        seen_files.add(key)
        try:
            st = os.stat(f)
            cached = _file_cache.get(key)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                records.extend(cached[2])
            else:
                recs = parse_codex_jsonl(f)
                _file_cache[key] = (st.st_mtime, st.st_size, recs)
                records.extend(recs)
        except OSError:
            records.extend(parse_codex_jsonl(f))

    # Purge entries for files that no longer exist
    stale = set(_file_cache) - seen_files
    for k in stale:
        del _file_cache[k]

    with _cache_lock:
        _cache = records
        _cache_time = time.time()
    return _cache


def _bg_refresh():
    """Background cache refresh — non-blocking."""
    global _bg_refreshing
    try:
        _load_cache()
    finally:
        with _cache_lock:
            _bg_refreshing = False


def _get_records(force: bool = False) -> list:
    """Return cached records. Never blocks on cache hit.

    - ``force=True`` → synchronous reload (explicit refresh).
    - Cache warm → return immediately.
    - Cache stale → return stale data, trigger background refresh.
    - Cache empty → block until first load completes.
    """
    global _bg_refreshing
    import time
    if force:
        _load_cache()
        return _cache
    with _cache_lock:
        if _cache:
            if (time.time() - _cache_time) > CACHE_TTL and not _bg_refreshing:
                _bg_refreshing = True
                threading.Thread(target=_bg_refresh, daemon=True).start()
            return _cache
    # Empty cache — must block for first load
    _load_cache()
    return _cache


def _apply_time_filter(records: list, time: str, start: str, end: str, tz_offset: int = 8):
    """Filter *records* by *time* preset or custom [*start*, *end*] datetime range.

    When *time* is ``"custom"``, *start* and/or *end* are parsed as local
    datetime strings in the user's timezone (identified by *tz_offset*) and
    used as inclusive bounds.  Otherwise ``get_time_cutoff(time, tz_offset)``
    is used as a lower bound.
    """
    from datetime import datetime, timezone as _tz, timedelta as _td
    user_tz = _tz(_td(hours=tz_offset))
    if time == "custom":
        if start:
            try:
                s = datetime.fromisoformat(start)
                if s.tzinfo is None:
                    s = s.replace(tzinfo=user_tz)
                records = [r for r in records if r.timestamp >= s]
            except (ValueError, TypeError):
                pass
        if end:
            try:
                e = datetime.fromisoformat(end)
                if e.tzinfo is None:
                    e = e.replace(tzinfo=user_tz)
                records = [r for r in records if r.timestamp <= e]
            except (ValueError, TypeError):
                pass
        return records
    # Preset filter: "all" | "today" | "7d" | "30d"
    cutoff = get_time_cutoff(time, tz_offset)
    return [r for r in records if r.timestamp >= cutoff]


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/api/sources")
def api_sources():
    """Return list of available data sources found in parsed records.

    Each source has a raw key (e.g. ``\"claude\"``) and a display label
    (e.g. ``\"Claude Code\"``).  Labels are auto-generated from the raw
    key when no explicit mapping exists.
    """
    records = _get_records()
    sources = sorted({r.data_source for r in records if r.data_source})

    # Friendly labels — extend this mapping for new parsers
    LABELS: dict[str, str] = {
        "claude": "Claude Code",
        "hermes": "Hermes",
        "codex": "Codex",
    }

    return {
        "sources": [
            {"key": s, "label": LABELS.get(s, s.title())}
            for s in sources
        ]
    }


@app.get("/api/profiles")
def api_profiles():
    """Return list of available Hermes profiles found in the data."""
    records = _get_records()
    profiles = sorted({
        (r.profile or "default")
        for r in records if r.data_source == "hermes"
    })
    return {"profiles": profiles}


@app.get("/api/models")
def api_models(source: str = Query(""), profile: str = Query(""), time: str = Query(""),
               start: str = Query(""), end: str = Query(""), tz: int = Query(8)):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if time:
        records = _apply_time_filter(records, time, start, end, tz)
    models = get_available_models(records)
    counts = {m: sum(r.api_call_count for r in records if r.model == m) for m in models}

    # Per-source model breakdown (when no source filter)
    by_source: dict[str, dict] = {}
    if not source:
        for r in records:
            s = r.data_source
            if s not in by_source:
                by_source[s] = {"source": s, "models": {}, "total_requests": 0}
            by_source[s]["total_requests"] += r.api_call_count
            by_source[s]["models"][r.model] = by_source[s]["models"].get(r.model, 0) + r.api_call_count

    return {
        "models": [{"name": m, "count": counts[m]} for m in models],
        "total": sum(r.api_call_count for r in records),
        "by_source": [
            {"source": s, "total_requests": d["total_requests"],
             "models": [{"name": m, "count": c} for m, c in sorted(d["models"].items())]}
            for s, d in sorted(by_source.items(), key=lambda x: -x[1]["total_requests"])
        ] if not source else [],
    }


@app.get("/api/stats")
def api_stats(time: str = Query("all"), model: str = Query(""), source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
              start: str = Query(""), end: str = Query(""), tz: int = Query(8)):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]
    user_count_time = time
    if time == "custom":
        records = _apply_time_filter(records, "custom", start, end, tz)
        time = "all"
    stats = aggregate_by_model_date(records, time, tz)

    if model:
        stats = [s for s in stats if s.model == model]

    # 获取每个模型的用户输入次数
    user_input_counts = _get_user_input_counts(
        tz, user_count_time, start, end, source, profile, agent, model
    )

    result = []
    for s in stats:
        result.append({
            "model": s.model,
            "date": s.date,
            "input": s.total_input,
            "output": s.total_output + s.total_reasoning,
            "cache_read": s.total_cache_read,
            "cache_create": s.total_cache_creation,
            "requests": s.request_count,
            "requests_cache": s.requests_with_cache,
            "hit_rate": round(s.cache_hit_rate, 1),
            "token_hit_rate": round(s.token_hit_rate, 1),
            "cost": round(s.estimated_cost, 4),
            "user_inputs": user_input_counts.get(s.model, 0),
        })
    return result


@app.get("/api/summary")
def api_summary(time: str = Query("all"), model: str = Query(""), source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
                start: str = Query(""), end: str = Query(""), tz: int = Query(8)):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]
    user_count_time = time
    if time == "custom":
        records = _apply_time_filter(records, "custom", start, end, tz)
        time = "all"
    stats = aggregate_by_model_date(records, time, tz)
    if model:
        stats = [s for s in stats if s.model == model]

    ti = sum(s.total_input for s in stats)
    to = sum(s.total_output + s.total_reasoning for s in stats)
    tcr = sum(s.total_cache_read for s in stats)
    tcc = sum(s.total_cache_creation for s in stats)
    tc = sum(s.estimated_cost for s in stats)
    tr = sum(s.request_count for s in stats)
    trc = sum(s.requests_with_cache for s in stats)
    hit = round(trc / tr * 100, 1) if tr > 0 else 0
    token_hit = round(tcr / ti * 100, 1) if ti > 0 else 0

    # 获取用户输入次数
    user_input_counts = _get_user_input_counts(
        tz, user_count_time, start, end, source, profile, agent, model
    )
    total_user_inputs = sum(user_input_counts.get(s.model, 0) for s in stats)

    return {
        "input": ti,
        "output": to,
        "cache_read": tcr,
        "cache_create": tcc,
        "cost": round(tc, 2),
        "requests": tr,
        "hit_rate": hit,
        "token_hit_rate": token_hit,
        "groups": len(stats),
        "user_inputs": total_user_inputs,
        "by_source": _compute_by_source_summary(records, time, tz) if not source else [],
    }


def _compute_by_source_summary(records: list, time: str, tz_offset: int = 8) -> list[dict]:
    """Compute per-source aggregated summary."""
    from collections import defaultdict
    filtered = _apply_time_filter(records, time, "", "", tz_offset)
    src: dict[str, dict] = {}
    for r in filtered:
        s = r.data_source or "unknown"
        if s not in src:
            src[s] = {"source": s, "requests": 0, "input": 0, "output": 0,
                       "cache_read": 0, "cache_creation": 0,
                       "cost": 0.0, "models": {}}
        d = src[s]
        d["requests"] += r.api_call_count
        d["input"] += r.input_tokens
        d["output"] += r.output_tokens
        d["cache_read"] += r.cache_read
        d["cache_creation"] += r.cache_creation
        d["output"] += getattr(r, "reasoning_tokens", 0)
        in_price, out_price, cr_price = get_model_price(r.model)
        d["cost"] += ((max(0, r.input_tokens - r.cache_read) / 1_000_000 * in_price
                      + r.cache_read / 1_000_000 * cr_price
                      + r.output_tokens / 1_000_000 * out_price
                      + getattr(r, "reasoning_tokens", 0) / 1_000_000 * out_price))
        # Per-model counts within source
        d["models"][r.model] = d["models"].get(r.model, 0) + r.api_call_count
    result = []
    for s, d in sorted(src.items(), key=lambda x: -x[1]["requests"]):
        result.append({
            "source": s,
            "requests": d["requests"],
            "input": d["input"],
            "output": d["output"],
            "cache_read": d["cache_read"],
            "cache_creation": d["cache_creation"],
            "cost": round(d["cost"], 2),
            "models": [{"name": m, "count": c} for m, c
                       in sorted(d["models"].items(), key=lambda x: -x[1])],
        })
    return result


@app.get("/api/logs")
def api_logs(time: str = Query("all"), model: str = Query(""),
             source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
             start: str = Query(""), end: str = Query(""),
             page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500),
             tz: int = Query(8)):
    """Return paginated raw TokenUsage records with timestamps."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]

    filtered = _apply_time_filter(records, time, start, end, tz)
    if model:
        filtered = [r for r in filtered if r.model == model]

    filtered.sort(key=lambda r: r.timestamp, reverse=True)
    total = len(filtered)
    start = (page - 1) * limit
    end = start + limit
    page_records = filtered[start:end]

    items = []
    for r in page_records:
        in_price, out_price, cr_price = get_model_price(r.model)
        cost = round(max(0, r.input_tokens - r.cache_read) / 1_000_000 * in_price
                     + r.cache_read / 1_000_000 * cr_price
                     + r.output_tokens / 1_000_000 * out_price
                     + getattr(r, "reasoning_tokens", 0) / 1_000_000 * out_price, 6)
        items.append({
            "request_id": r.request_id,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens + getattr(r, "reasoning_tokens", 0),
            "cache_read": r.cache_read,
            "cache_creation": r.cache_creation,
            "timestamp": r.timestamp.isoformat(),
            "cost": cost,
            "data_source": r.data_source,
            "profile": r.profile,
            "status_code": r.status_code,
            "latency_ms": r.latency_ms,
            "first_token_ms": r.first_token_ms,
        })

    return {"items": items, "total": total, "page": page, "limit": limit}


@app.get("/api/trends")
def api_trends(time: str = Query("30d"), source: str = Query(""), profile: str = Query(""), model: str = Query(""), agent: str = Query(""),
               start: str = Query(""), end: str = Query(""), tz: int = Query(8)):
    """Return daily aggregated data for charts: [{date, requests, input, output, cache_read, cost}]."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if model:
        records = [r for r in records if r.model == model]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]
    if time == "custom":
        records = _apply_time_filter(records, "custom", start, end, tz)
        time = "all"
    stats = aggregate_by_model_date(records, time, tz)

    daily: dict[str, dict] = {}
    for s in stats:
        d = daily.setdefault(s.date, {
            "date": s.date, "requests": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_creation": 0, "cost": 0.0,
        })
        d["requests"] += s.request_count
        d["input"] += s.total_input
        d["output"] += s.total_output + s.total_reasoning
        d["cache_read"] += s.total_cache_read
        d["cache_creation"] += s.total_cache_creation
        d["cost"] += round(s.estimated_cost, 4)
    result = sorted(daily.values(), key=lambda x: x["date"])
    return result


@app.get("/api/providers")
def api_providers(time: str = Query("all"), model: str = Query(""), source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
                  start: str = Query(""), end: str = Query(""), tz: int = Query(8)):
    """Return per-provider aggregated stats.

    Provider is extracted from each record's model field via
    ``extract_provider``.  Results are aggregated across the selected
    time range and optionally filtered to a single model."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]

    filtered = _apply_time_filter(records, time, start, end, tz)
    if model:
        filtered = [r for r in filtered if r.model == model]

    # Group by provider
    prov: dict[str, dict[str, Any]] = {}
    for r in filtered:
        p = extract_provider(r.model)
        if p not in prov:
            prov[p] = {
                "provider": p,
                "request_count": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "total_cache_read": 0,
                "total_cache_creation": 0,
                "total_cost": 0.0,
                "latencies": [],
                "success_count": 0,
                "models": set(),
            }
        d = prov[p]
        d["request_count"] += r.api_call_count  # 使用实际API调用次数
        d["total_input_tokens"] += r.input_tokens
        d["total_output_tokens"] += r.output_tokens
        d["total_cache_read"] += r.cache_read
        d["total_cache_creation"] += r.cache_creation
        d["total_output_tokens"] += getattr(r, "reasoning_tokens", 0)
        d["models"].add(r.model)
        if r.status_code == 200:
            d["success_count"] += r.api_call_count  # 与request_count一致
        if r.latency_ms > 0:
            d["latencies"].append(r.latency_ms)
        in_price, out_price, cr_price = get_model_price(r.model)
        d["total_cost"] += (
            max(0, r.input_tokens - r.cache_read) / 1_000_000 * in_price
            + r.cache_read / 1_000_000 * cr_price
            + r.output_tokens / 1_000_000 * out_price
            + getattr(r, "reasoning_tokens", 0) / 1_000_000 * out_price
        )

    result = []
    for p_name, d in prov.items():
        if d["total_cost"] <= 0:
            continue
        success_rate = round(
            d["success_count"] / d["request_count"] * 100, 1
        ) if d["request_count"] > 0 else 100.0
        avg_latency = round(
            sum(d["latencies"]) / len(d["latencies"]), 2
        ) if d["latencies"] else 0.0
        result.append({
            "provider": p_name,
            "request_count": d["request_count"],
            "total_input_tokens": d["total_input_tokens"],
            "total_output_tokens": d["total_output_tokens"],
            "total_cache_read": d["total_cache_read"],
            "total_cache_creation": d["total_cache_creation"],
            "total_cost": round(d["total_cost"], 4),
            "success_rate": success_rate,
            "avg_latency_ms": avg_latency,
            "models": sorted(d["models"]),
        })

    result.sort(key=lambda x: x["total_cost"], reverse=True)
    return result


@app.get("/api/agents")
def api_agents(source: str = Query(""), profile: str = Query("")):
    """List all agents (entrypoint/source field) with request counts."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]

    # Count by agent, tracking which source each agent appears in
    agent_counts: dict[str, dict] = {}
    for r in records:
        agent = r.agent or "unknown"
        if agent not in agent_counts:
            agent_counts[agent] = {"name": agent, "count": 0, "source": r.data_source}
        agent_counts[agent]["count"] += 1

    agents = sorted(agent_counts.values(), key=lambda x: -x["count"])
    return {"agents": agents}


@app.get("/api/agent-stats")
def api_agent_stats(time: str = Query("all"), model: str = Query(""),
                    source: str = Query(""), profile: str = Query(""),
                    agent: str = Query(""),
                    start: str = Query(""), end: str = Query(""),
                    tz: int = Query(8)):
    """Agent × model cross statistics — one row per (agent, model) combination."""
    from collections import defaultdict

    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]

    filtered = _apply_time_filter(records, time, start, end, tz)
    if model:
        filtered = [r for r in filtered if r.model == model]
    if agent:
        filtered = [r for r in filtered if (r.agent or "unknown") == agent]

    # Group by (agent, agent_source, model)
    groups: dict[tuple, dict] = defaultdict(lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_creation": 0,
        "requests": 0, "requests_cache": 0,
    })

    for r in filtered:
        agent = r.agent or "unknown"
        key = (agent, r.data_source, r.model)
        d = groups[key]
        d["input"] += r.input_tokens
        d["output"] += r.output_tokens
        d["cache_read"] += r.cache_read
        d["cache_creation"] += r.cache_creation
        d["requests"] += 1
        if r.cache_read > 0:
            d["requests_cache"] += 1

    result = []
    for (agent, agent_source, model_name), d in groups.items():
        hit_rate = round(d["requests_cache"] / d["requests"] * 100, 1) if d["requests"] > 0 else 0
        in_price, out_price, cr_price = get_model_price(model_name)
        cost = round(
            max(0, d["input"] - d["cache_read"]) / 1_000_000 * in_price
            + d["cache_read"] / 1_000_000 * cr_price
            + d["output"] / 1_000_000 * out_price, 4
        )
        result.append({
            "agent": agent,
            "agent_source": agent_source,
            "model": model_name,
            "input": d["input"],
            "output": d["output"],
            "cache_read": d["cache_read"],
            "cache_creation": d["cache_creation"],
            "requests": d["requests"],
            "requests_cache": d["requests_cache"],
            "hit_rate": hit_rate,
            "cost": cost,
        })

    result.sort(key=lambda x: (x["agent"], x["model"]))
    return result


@app.get("/api/pricing")
def api_get_pricing():
    """Return current MODEL_PRICING as a JSON array."""
    return [prices.to_row(name) for name, prices in MODEL_PRICING.items()]


class PricingEntry(BaseModel):
    """Single pricing override entry from the request body."""
    model: str
    input_price: float
    output_price: float
    cache_read_price: float = 0.0
    cache_write_price: float = 0.0


@app.put("/api/pricing")
def api_update_pricing(body: list[PricingEntry]):
    """Update in-memory MODEL_PRICING from a JSON array.

    Each entry must specify ``model``, ``input_price``, and
    ``output_price``; ``cache_read_price`` and ``cache_write_price``
    default to 0.
    """
    from hermes_token_dash.models import ModelPricing
    for entry in body:
        MODEL_PRICING[entry.model] = ModelPricing(
            input_price=entry.input_price,
            output_price=entry.output_price,
            cache_read_price=entry.cache_read_price,
            cache_write_price=entry.cache_write_price,
        )
    return {"ok": True, "updated": len(body)}


@app.post("/api/refresh")
def api_refresh():
    _load_cache()
    return {"ok": True}


@app.get("/api/settings")
def api_get_settings():
    """Return current settings: exchange rate."""
    return {"exchange_rate": models.EXCHANGE_RATE}


class SettingsUpdate(BaseModel):
    exchange_rate: float | None = None


@app.put("/api/settings")
def api_update_settings(body: SettingsUpdate):
    """Update runtime settings."""
    from hermes_token_dash import models
    if body.exchange_rate is not None and body.exchange_rate > 0:
        models.EXCHANGE_RATE = body.exchange_rate
    return {"ok": True, "exchange_rate": models.EXCHANGE_RATE}


@app.get("/api/balance/deepseek")
def api_balance_deepseek():
    """Query DeepSeek balance via official API."""
    import json
    import os
    import urllib.error
    import urllib.request
    
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # 回退：从 Windows 用户环境变量读取（MSYS 终端可能读不到）
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                api_key, _ = winreg.QueryValueEx(key, "DEEPSEEK_API_KEY")
        except Exception:
            pass
    if not api_key:
        # 回退：从配置文件读取
        key_file = Path(__file__).parent.parent / "deepseek_key.txt"
        if key_file.exists():
            api_key = key_file.read_text().strip()
    
    if not api_key:
        return {"status": "no_key", "message": "未配置 API Key"}
    
    try:
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = resp.status
            data = json.loads(resp.read().decode("utf-8"))
        
        if status_code == 401 or data.get("code") == 401:
            return {"status": "invalid_key", "message": "API Key 无效"}
        
        if "balance_infos" in data and data["balance_infos"]:
            info = data["balance_infos"][0]
            return {
                "status": "ok",
                "balances": {
                    "total": float(info.get("total_balance", 0)),
                    "granted": float(info.get("granted_balance", 0)),
                    "topped_up": float(info.get("topped_up_balance", 0)),
                },
                "currency": info.get("currency", "CNY"),
                "is_available": data.get("is_available", True)
            }
        
        return {"status": "error", "message": str(data)}
        
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"status": "invalid_key", "message": "Invalid API Key"}
        body = e.read().decode("utf-8", errors="replace")
        return {"status": "error", "message": body or str(e)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/api/balance/deepseek/key")
def api_save_deepseek_key(key: str = ""):
    """Save DeepSeek API key to file."""
    key_file = Path(__file__).parent.parent / "deepseek_key.txt"
    key_file.write_text(key.strip())
    return {"ok": True}


def _mimo_login_sync():
    """Synchronous MiMo login - runs in thread pool."""
    import json as _json
    import os
    import time
    from playwright.sync_api import sync_playwright
    
    cookie_file = Path(__file__).parent.parent / "mimo_cookies.json"
    BALANCE_URL = "https://platform.xiaomimimo.com/#/console/balance"
    
    exe = None
    home = Path.home()
    for p in [
        home / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(p):
            exe = p
            break
    
    with sync_playwright() as pw:
        kw = {"executable_path": exe} if exe else {}
        browser = pw.chromium.launch(headless=False, **kw)
        context = browser.new_context()
        page = context.new_page()
        page.goto(BALANCE_URL)
        # Wait for redirect to complete before checking URL (avoid race condition)
        page.wait_for_timeout(3000)
        
        start = time.time()
        while time.time() - start < 300:
            url = page.url
            if "platform.xiaomimimo.com" in url and "account.xiaomi.com" not in url:
                page.wait_for_load_state("networkidle", timeout=30000)
                page.wait_for_timeout(3000)
                cookies = context.cookies()
                mimo = [c for c in cookies if "xiaomimimo" in c.get("domain", "") or "xiaomi" in c.get("domain", "")]
                cookie_file.write_text(_json.dumps(mimo, indent=2, ensure_ascii=False))
                browser.close()
                return {"status": "ok", "message": f"登录成功，已保存 {len(mimo)} 个 Cookie"}
            page.wait_for_timeout(2000)
        
        browser.close()
        return {"status": "timeout", "message": "登录超时（5分钟）"}


@app.get("/api/balance/login")
async def api_balance_login():
    """Open browser for MiMo login, auto-save cookies when logged in (thread-safe)."""
    import asyncio
    try:
        result = await asyncio.to_thread(_mimo_login_sync)
        return result
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _query_mimo_balance_sync(cookies):
    """Synchronous MiMo balance query - runs in thread pool."""
    import re
    import os
    from playwright.sync_api import sync_playwright
    
    exe = None
    home = Path.home()
    for p in [
        home / "AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(p):
            exe = p
            break
    
    with sync_playwright() as pw:
        kw = {"executable_path": exe} if exe else {}
        browser = pw.chromium.launch(headless=True, **kw)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        page.goto("https://platform.xiaomimimo.com/#/console/balance")
        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(8000)
        
        if "account.xiaomi.com" in page.url:
            browser.close()
            return {"status": "expired", "message": "Cookie 已过期，请重新登录"}

        # Get full text and HTML for parsing
        body_text = page.evaluate("document.body.innerText")

        # Check if page content indicates login page (overlay without redirect)
        login_indicators = ["请输入密码", "短信登录", "扫码登录"]
        if any(ind in body_text for ind in login_indicators):
            browser.close()
            return {"status": "expired", "message": "需要重新登录"}

        html = page.content()
        browser.close()
    
    result = {"status": "ok", "balances": {}}
    
    # Patterns: Chinese + English labels, with optional ¥/￥ prefix
    # Order: specific patterns first, general patterns last
    patterns = [
        # Chinese labels
        (r'(?:充值余额|现金余额|可用余额)[：:\s]*[¥￥]?\s*([\d,.]+)', 'cash'),
        (r'(?:赠送余额|奖励余额|免费额度)[：:\s]*[¥￥]?\s*([\d,.]+)', 'bonus'),
        (r'(?:账户余额|总余额|余额)[：:\s]*[¥￥]?\s*([\d,.]+)', 'total'),
        # English labels
        (r'(?:Cash Balance|Recharged Balance|Available Balance)[：:\s]*[¥￥\$]?\s*([\d,.]+)', 'cash'),
        (r'(?:Bonus Balance|Gift Balance|Free Credits|Granted)[：:\s]*[¥￥\$]?\s*([\d,.]+)', 'bonus'),
        (r'(?:Total Balance|Account Balance|Balance)[：:\s]*[¥￥\$]?\s*([\d,.]+)', 'total'),
        # Generic: any number after ¥/￥ symbol
        (r'[¥￥]\s*([\d,.]+)', 'total'),
    ]
    
    for pat, key in patterns:
        m = re.search(pat, body_text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(",", ""))
            if key not in result["balances"]:  # first match wins per key
                result["balances"][key] = val
    
    # Also search HTML for balance data (e.g. data attributes, JSON)
    if not result["balances"]:
        # Try to find number patterns in HTML near balance-related text
        html_pat = re.findall(r'[¥￥]([\d,.]+)', html)
        if html_pat:
            result["balances"]["total"] = float(html_pat[0].replace(",", ""))
    
    if not result["balances"]:
        result["status"] = "parse_error"
        result["message"] = "无法解析余额数据"
        result["body_text"] = body_text[:8000]
    
    return result


@app.get("/api/balance")
async def api_balance():
    """Query MiMo balance using saved cookies (thread-safe)."""
    import asyncio
    import json as _json
    cookie_file = Path(__file__).parent.parent / "mimo_cookies.json"
    
    if not cookie_file.exists():
        return {"status": "no_cookies", "message": "未登录，请先运行 mimo_balance.py 登录"}
    
    try:
        cookies = _json.loads(cookie_file.read_text(encoding="utf-8"))
        if not cookies:
            return {"status": "no_cookies", "message": "Cookie 为空，请重新登录"}
    except Exception as e:
        return {"status": "error", "message": f"Cookie 文件读取失败: {e}"}
    
    try:
        result = await asyncio.to_thread(_query_mimo_balance_sync, cookies)
        return result
    except Exception as e:
        return {"status": "error", "message": f"查询失败: {str(e)}"}


def main():
    import uvicorn
    webbrowser.open("http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
