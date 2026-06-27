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

def _get_user_input_counts() -> dict[str, int]:
    """Get user input count per model from Hermes messages table."""
    import sqlite3
    import os
    counts = {}
    db_path = os.path.expanduser("~/AppData/Local/hermes/state.db")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("""
            SELECT s.model, COUNT(*) as cnt
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.role = 'user' AND s.model IS NOT NULL
            GROUP BY s.model
        """)
        for row in cur.fetchall():
            counts[row[0]] = row[1]
        conn.close()
    except Exception:
        pass
    return counts

app = FastAPI(title="Hermes Token Dashboard")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

STATIC = Path(__file__).parent / "static"
STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# Cache parsed data with TTL (auto-refresh after 30 seconds)
_cache: list = []
_cache_time: float = 0.0
CACHE_TTL: float = 30.0  # seconds


@app.on_event("startup")
async def _preload():
    """Pre-load data on startup so first request is instant."""
    import asyncio
    await asyncio.to_thread(_load_cache)


def _load_cache() -> list:
    """Scan and parse all JSONL files + Hermes session DBs + Codex sessions.  Cached until next refresh."""
    global _cache, _cache_time
    import time
    records = []
    # Claude Code
    for f in scan_claude_jsonls():
        records.extend(parse_jsonl(f))
    # Hermes Agent
    records.extend(parse_hermes_sessions())
    # Codex CLI
    for f in scan_codex_jsonls():
        records.extend(parse_codex_jsonl(f))
    _cache = records
    _cache_time = time.time()
    return _cache


def _get_records(force: bool = False) -> list:
    global _cache, _cache_time
    import time
    if force or not _cache or (time.time() - _cache_time) > CACHE_TTL:
        _load_cache()
    return _cache


def _apply_time_filter(records: list, time: str, start: str, end: str):
    """Filter *records* by *time* preset or custom [*start*, *end*] ISO range.

    When *time* is ``\"custom\"``, *start* and/or *end* (ISO-8601
    datetime strings) are parsed as UTC and used as inclusive bounds.
    Otherwise ``get_time_cutoff(time)`` is used as a lower bound.
    """
    from datetime import datetime, timezone as _tz
    if time == "custom":
        if start:
            try:
                s = datetime.fromisoformat(start)
                if s.tzinfo is None:
                    s = s.replace(tzinfo=_tz.utc)
                records = [r for r in records if r.timestamp >= s]
            except (ValueError, TypeError):
                pass
        if end:
            try:
                e = datetime.fromisoformat(end)
                if e.tzinfo is None:
                    e = e.replace(tzinfo=_tz.utc)
                records = [r for r in records if r.timestamp <= e]
            except (ValueError, TypeError):
                pass
        return records
    # Preset filter: "all" | "today" | "7d" | "30d"
    cutoff = get_time_cutoff(time)
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
               start: str = Query(""), end: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if time:
        records = _apply_time_filter(records, time, start, end)
    models = get_available_models(records)
    counts = {m: sum(1 for r in records if r.model == m) for m in models}

    # Per-source model breakdown (when no source filter)
    by_source: dict[str, dict] = {}
    if not source:
        for r in records:
            s = r.data_source
            if s not in by_source:
                by_source[s] = {"source": s, "models": {}, "total_requests": 0}
            by_source[s]["total_requests"] += 1
            by_source[s]["models"][r.model] = by_source[s]["models"].get(r.model, 0) + 1

    return {
        "models": [{"name": m, "count": counts[m]} for m in models],
        "total": len(records),
        "by_source": [
            {"source": s, "total_requests": d["total_requests"],
             "models": [{"name": m, "count": c} for m, c in sorted(d["models"].items())]}
            for s, d in sorted(by_source.items(), key=lambda x: -x[1]["total_requests"])
        ] if not source else [],
    }


@app.get("/api/stats")
def api_stats(time: str = Query("all"), model: str = Query(""), source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
              start: str = Query(""), end: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]
    if time == "custom":
        records = _apply_time_filter(records, "custom", start, end)
        time = "all"
    stats = aggregate_by_model_date(records, time)

    if model:
        stats = [s for s in stats if s.model == model]

    # 获取每个模型的用户输入次数
    user_input_counts = _get_user_input_counts()

    result = []
    for s in stats:
        result.append({
            "model": s.model,
            "date": s.date,
            "input": s.total_input,
            "output": s.total_output,
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
                start: str = Query(""), end: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]
    if time == "custom":
        records = _apply_time_filter(records, "custom", start, end)
        time = "all"
    stats = aggregate_by_model_date(records, time)
    if model:
        stats = [s for s in stats if s.model == model]

    ti = sum(s.total_input for s in stats)
    to = sum(s.total_output for s in stats)
    tcr = sum(s.total_cache_read for s in stats)
    tcc = sum(s.total_cache_creation for s in stats)
    tc = sum(s.estimated_cost for s in stats)
    tr = sum(s.request_count for s in stats)
    trc = sum(s.requests_with_cache for s in stats)
    hit = round(trc / tr * 100, 1) if tr > 0 else 0
    token_hit = round(tcr / ti * 100, 1) if ti > 0 else 0

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
        "by_source": _compute_by_source_summary(records, time) if not source else [],
    }


def _compute_by_source_summary(records: list, time: str) -> list[dict]:
    """Compute per-source aggregated summary."""
    from collections import defaultdict
    filtered = _apply_time_filter(records, time, "", "")

    src: dict[str, dict] = {}
    for r in filtered:
        s = r.data_source or "unknown"
        if s not in src:
            src[s] = {"source": s, "requests": 0, "input": 0, "output": 0,
                       "cache_read": 0, "cache_creation": 0, "cost": 0.0,
                       "models": {}}
        d = src[s]
        d["requests"] += 1
        d["input"] += r.input_tokens
        d["output"] += r.output_tokens
        d["cache_read"] += r.cache_read
        d["cache_creation"] += r.cache_creation
        in_price, out_price, cr_price = get_model_price(r.model)
        d["cost"] += ((max(0, r.input_tokens - r.cache_read) / 1_000_000 * in_price
                      + r.cache_read / 1_000_000 * cr_price
                      + r.output_tokens / 1_000_000 * out_price))
        # Per-model counts within source
        d["models"][r.model] = d["models"].get(r.model, 0) + 1

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
             page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500)):
    """Return paginated raw TokenUsage records with timestamps."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]
    if agent:
        records = [r for r in records if (r.agent or "unknown") == agent]

    filtered = _apply_time_filter(records, time, start, end)
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
                     + r.output_tokens / 1_000_000 * out_price, 6)
        items.append({
            "request_id": r.request_id,
            "model": r.model,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
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
               start: str = Query(""), end: str = Query("")):
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
        records = _apply_time_filter(records, "custom", start, end)
        time = "all"
    stats = aggregate_by_model_date(records, time)

    daily: dict[str, dict] = {}
    for s in stats:
        d = daily.setdefault(s.date, {
            "date": s.date, "requests": 0, "input": 0, "output": 0,
            "cache_read": 0, "cache_creation": 0, "cost": 0.0,
        })
        d["requests"] += s.request_count
        d["input"] += s.total_input
        d["output"] += s.total_output
        d["cache_read"] += s.total_cache_read
        d["cache_creation"] += s.total_cache_creation
        d["cost"] += round(s.estimated_cost, 4)

    result = sorted(daily.values(), key=lambda x: x["date"])
    return result


@app.get("/api/providers")
def api_providers(time: str = Query("all"), model: str = Query(""), source: str = Query(""), profile: str = Query(""), agent: str = Query(""),
                  start: str = Query(""), end: str = Query("")):
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

    filtered = _apply_time_filter(records, time, start, end)
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
                    start: str = Query(""), end: str = Query("")):
    """Agent × model cross statistics — one row per (agent, model) combination."""
    from collections import defaultdict

    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if profile:
        records = [r for r in records if r.profile == profile]

    filtered = _apply_time_filter(records, time, start, end)
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
    import os
    import requests as _req
    
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
        resp = _req.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10
        )
        data = resp.json()
        
        if resp.status_code == 401:
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
    for p in [
        r"C:\Users\20107\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
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
    for p in [
        r"C:\Users\20107\AppData\Local\ms-playwright\chromium-1228\chrome-win64\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
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
