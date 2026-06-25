"""Hermes Token Dashboard — FastAPI server.

Serves token usage data via REST API and the Vue 3 frontend.
"""

from __future__ import annotations

import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from hermes_token_dash.models import (
    MODEL_PRICING,
    extract_provider,
    get_model_price,
)
from hermes_token_dash.parser_claude import (
    aggregate_by_model_date,
    get_available_models,
    parse_jsonl,
    scan_claude_jsonls,
)
from hermes_token_dash.parser_hermes import parse_hermes_sessions

app = FastAPI(title="Hermes Token Dashboard")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

STATIC = Path(__file__).parent / "static"
STATIC.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# Cache parsed data
_cache: list = []
_cache_time: str = ""


@app.on_event("startup")
async def _preload():
    """Pre-load data on startup so first request is instant."""
    import asyncio
    await asyncio.to_thread(_load_cache)


def _load_cache() -> list:
    """Scan and parse all JSONL files + Hermes session DBs.  Cached until next refresh."""
    global _cache
    records = []
    # Claude Code
    for f in scan_claude_jsonls():
        records.extend(parse_jsonl(f))
    # Hermes Agent
    records.extend(parse_hermes_sessions())
    _cache = records
    return _cache


def _get_records(force: bool = False) -> list:
    global _cache
    if force or not _cache:
        _load_cache()
    return _cache


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/api/models")
def api_models(source: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    models = get_available_models(records)
    counts = {m: sum(1 for r in records if r.model == m) for m in models}
    return {
        "models": [{"name": m, "count": counts[m]} for m in models],
        "total": len(records),
    }


@app.get("/api/stats")
def api_stats(time: str = Query("all"), model: str = Query(""), source: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    stats = aggregate_by_model_date(records, time)

    if model:
        stats = [s for s in stats if s.model == model]

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
            "cost": round(s.estimated_cost, 4),
        })
    return result


@app.get("/api/summary")
def api_summary(time: str = Query("all"), model: str = Query(""), source: str = Query("")):
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    stats = aggregate_by_model_date(records, time)
    if model:
        stats = [s for s in stats if s.model == model]

    ti = sum(s.total_input for s in stats)
    to = sum(s.total_output for s in stats)
    tc = sum(s.estimated_cost for s in stats)
    tr = sum(s.request_count for s in stats)
    trc = sum(s.requests_with_cache for s in stats)
    hit = round(trc / tr * 100, 1) if tr > 0 else 0

    return {
        "input": ti,
        "output": to,
        "cost": round(tc, 2),
        "requests": tr,
        "hit_rate": hit,
        "groups": len(stats),
    }


@app.get("/api/logs")
def api_logs(time: str = Query("all"), model: str = Query(""),
             source: str = Query(""),
             page: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=500)):
    """Return paginated raw TokenUsage records with timestamps."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    now = datetime.now(timezone.utc)
    today = now.date()

    cutoff = datetime.min.replace(tzinfo=timezone.utc)
    if time == "today":
        cutoff = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    elif time == "7d":
        cutoff = datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)
    elif time == "30d":
        cutoff = datetime.combine(today - timedelta(days=29), datetime.min.time(), tzinfo=timezone.utc)

    filtered = [r for r in records if r.timestamp >= cutoff]
    if model:
        filtered = [r for r in filtered if r.model == model]

    filtered.sort(key=lambda r: r.timestamp, reverse=True)
    total = len(filtered)
    start = (page - 1) * limit
    end = start + limit
    page_records = filtered[start:end]

    items = []
    for r in page_records:
        in_price, out_price = get_model_price(r.model)
        cost = round(r.input_tokens / 1_000_000 * in_price
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
            "status_code": r.status_code,
            "latency_ms": r.latency_ms,
            "first_token_ms": r.first_token_ms,
        })

    return {"items": items, "total": total, "page": page, "limit": limit}


@app.get("/api/trends")
def api_trends(time: str = Query("30d"), source: str = Query(""), model: str = Query("")):
    """Return daily aggregated data for charts: [{date, requests, input, output, cache_read, cost}]."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    if model:
        records = [r for r in records if r.model == model]
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
def api_providers(time: str = Query("all"), model: str = Query(""), source: str = Query("")):
    """Return per-provider aggregated stats.

    Provider is extracted from each record's model field via
    ``extract_provider``.  Results are aggregated across the selected
    time range and optionally filtered to a single model."""
    records = _get_records()
    if source:
        records = [r for r in records if r.data_source == source]
    now = datetime.now(timezone.utc)
    today = now.date()

    cutoff = datetime.min.replace(tzinfo=timezone.utc)
    if time == "today":
        cutoff = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    elif time == "7d":
        cutoff = datetime.combine(today - timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)
    elif time == "30d":
        cutoff = datetime.combine(today - timedelta(days=29), datetime.min.time(), tzinfo=timezone.utc)

    filtered = [r for r in records if r.timestamp >= cutoff]
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
        d["request_count"] += 1
        d["total_input_tokens"] += r.input_tokens
        d["total_output_tokens"] += r.output_tokens
        d["total_cache_read"] += r.cache_read
        d["total_cache_creation"] += r.cache_creation
        d["models"].add(r.model)
        if r.status_code == 200:
            d["success_count"] += 1
        if r.latency_ms > 0:
            d["latencies"].append(r.latency_ms)

        in_price, out_price = get_model_price(r.model)
        d["total_cost"] += (
            r.input_tokens / 1_000_000 * in_price
            + r.output_tokens / 1_000_000 * out_price
        )

    result = []
    for p_name, d in prov.items():
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


def main():
    import uvicorn
    webbrowser.open("http://127.0.0.1:8765")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
