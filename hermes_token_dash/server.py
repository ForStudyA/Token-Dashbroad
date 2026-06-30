"""Hermes Token Dashboard — FastAPI server.

Serves token usage data via REST API and the Vue 3 frontend.
"""

from __future__ import annotations

import json
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
)
from hermes_token_dash.proxy_db import (
    get_default_provider,
    get_provider,
    get_provider_by_name,
    get_active_mapping,
    set_active_mapping,
    get_proxy_enabled,
    delete_provider,
    delete_mapping,
    toggle_provider,
    toggle_mapping,
    insert_request_log,
    list_mappings,
    list_providers,
    normalize_usage,
    parse_proxy_request_logs,
    proxy_log_rows,
    set_proxy_enabled,
    upsert_mapping,
    upsert_provider,
)


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
    """User input counts are not read from legacy Hermes logs anymore."""
    return {}


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
    """Load usage records from the proxy database."""
    global _cache, _cache_time

    records: list = parse_proxy_request_logs()

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


def _record_cost_cny(record) -> float:
    actual = getattr(record, "total_cost_cny", 0.0) or 0.0
    if actual > 0:
        return float(actual)
    in_price, out_price, cr_price = get_model_price(record.model)
    return (
        max(0, record.input_tokens - record.cache_read) / 1_000_000 * in_price
        + record.cache_read / 1_000_000 * cr_price
        + record.output_tokens / 1_000_000 * out_price
        + getattr(record, "reasoning_tokens", 0) / 1_000_000 * out_price
    )


@app.get("/")
def index():
    resp = FileResponse(str(STATIC / "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


class ProxyProviderBody(BaseModel):
    id: int | None = None
    name: str
    base_url: str
    api_key: str = ""
    enabled: bool = True


class ProxyMappingBody(BaseModel):
    id: int | None = None
    source_model: str
    target_model: str
    provider_id: int
    enabled: bool = True


class ProxyEnabledBody(BaseModel):
    enabled: bool


class ProxyActiveMappingBody(BaseModel):
    mode: str = "mapping"
    target_model: str = ""
    provider_id: int = 0
    mapping_id: int = 0


PROXY_URL = "http://127.0.0.1:8765/v1"


class RuntimeProxyProvider:
    """Provider settings resolved for a single proxied request."""

    def __init__(
        self,
        id: int | None,
        name: str,
        base_url: str,
        api_key: str = "",
        enabled: bool = True,
        auth_header: str = "",
    ) -> None:
        self.id = id
        self.name = name
        self.base_url = base_url
        self.api_key = api_key
        self.enabled = enabled
        self.auth_header = auth_header


def _toggle_agent_configs(enable_proxy: bool) -> None:
    """Toggle proxy for all registered agent adapters."""
    from hermes_token_dash.adapters import ADAPTERS
    for name, cls in ADAPTERS.items():
        adapter = cls()
        if not adapter.is_installed():
            continue
        if enable_proxy:
            adapter.set_proxy_url(PROXY_URL)
        else:
            adapter.restore_original()


def _upstream_chat_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _upstream_models_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def _upstream_model_url(base_url: str, model_id: str) -> str:
    from urllib.parse import quote

    return _upstream_models_url(base_url).rstrip("/") + "/" + quote(model_id, safe="")


def _upstream_show_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return base + "/api/show"


def _provider_headers(provider, accept: str = "application/json") -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": accept,
    }
    auth_header = getattr(provider, "auth_header", "")
    if auth_header:
        headers["Authorization"] = auth_header
    elif provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    return headers


def _request_auth_header(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    return auth.strip()


def _is_mimo_model(model: str) -> bool:
    value = (model or "").lower()
    return value.startswith("mimo-") or "/mimo-" in value or "xiaomi/mimo" in value


def _provider_with_request_auth(provider, auth_header: str):
    if not auth_header:
        return provider
    return RuntimeProxyProvider(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        api_key=provider.api_key,
        enabled=provider.enabled,
        auth_header=auth_header,
    )


def _mimo_provider_from_request(auth_header: str):
    provider = get_provider_by_name("mimo") or get_provider_by_name("xiaomi")
    if provider:
        return _provider_with_request_auth(provider, auth_header)
    return RuntimeProxyProvider(
        id=None,
        name="mimo",
        base_url="https://api.xiaomimimo.com/v1",
        auth_header=auth_header,
    )


def _select_chat_provider(request_model: str, auth_header: str):
    """Resolve the upstream provider/model for an incoming chat request."""
    if _is_mimo_model(request_model):
        return request_model, _mimo_provider_from_request(auth_header)

    active = get_active_mapping()
    mode = active.get("mode") or ("mapping" if active.get("target_model") else "")
    provider_id = int(active.get("provider_id") or 0)
    if not mode or not provider_id:
        return request_model, None

    provider = get_provider(provider_id)
    if not provider:
        return request_model, None
    provider = _provider_with_request_auth(provider, auth_header)

    if mode == "passthrough":
        return request_model, provider
    if mode == "mapping" and active.get("target_model"):
        return str(active["target_model"]), provider
    return request_model, None


def _model_list_fallback() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "created": 0, "owned_by": "proxy"}
            for model in sorted(MODEL_PRICING)
        ],
    }


def _model_fallback(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "proxy",
    }


def _show_fallback(model_id: str) -> dict[str, Any]:
    return {
        "name": model_id,
        "model": model_id,
        "details": {"family": "unknown"},
        "model_info": {},
        "capabilities": ["completion", "tools"],
    }


def _upstream_get_json(url: str, provider, fallback: dict[str, Any]) -> Response:
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(
            url,
            headers=_provider_headers(provider),
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return Response(
                content=resp.read(),
                status_code=resp.status,
                media_type=resp.headers.get_content_type() or "application/json",
            )
    except (urllib.error.HTTPError, Exception):
        return JSONResponse(content=fallback)


def _upstream_post_json(
    url: str,
    provider,
    body: dict[str, Any],
    fallback: dict[str, Any],
) -> Response:
    import urllib.error
    import urllib.request

    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=_provider_headers(provider),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return Response(
                content=resp.read(),
                status_code=resp.status,
                media_type=resp.headers.get_content_type() or "application/json",
            )
    except (urllib.error.HTTPError, Exception):
        return JSONResponse(content=fallback)


def _read_upstream_json(url: str, provider, method: str = "GET", body: dict[str, Any] | None = None) -> tuple[int, Any, str]:
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers=_provider_headers(provider),
            method=method,
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="ignore")
            try:
                return resp.status, json.loads(text), ""
            except Exception:
                return resp.status, text, ""
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        text = raw.decode("utf-8", errors="ignore")
        try:
            payload: Any = json.loads(text)
        except Exception:
            payload = text
        return exc.code, payload, _extract_error_text(payload) or text[:500]
    except Exception as exc:
        return 0, None, str(exc)


def _extract_model_ids(payload: Any) -> list[str]:
    values: list[str] = []
    items: Any = []
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), list):
            items = payload["data"]
        elif isinstance(payload.get("models"), list):
            items = payload["models"]
    elif isinstance(payload, list):
        items = payload
    for item in items:
        value = ""
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = str(item.get("id") or item.get("name") or item.get("model") or "")
        if value and value not in values:
            values.append(value)
    return values


def _get_active_provider_for_metadata():
    active = get_active_mapping()
    provider_id = int(active.get("provider_id") or 0)
    return get_provider(provider_id) if provider_id else None


def _normalize_chat_request_body(body: dict[str, Any], target_model: str) -> dict[str, Any]:
    """Normalize common OpenAI-ish request variants before upstream forwarding."""
    upstream_body = dict(body)
    upstream_body["model"] = target_model

    if "messages" not in upstream_body:
        text = upstream_body.get("input") or upstream_body.get("prompt")
        if isinstance(text, str) and text:
            messages = []
            instructions = upstream_body.get("instructions")
            if isinstance(instructions, str) and instructions:
                messages.append({"role": "system", "content": instructions})
            messages.append({"role": "user", "content": text})
            upstream_body["messages"] = messages
        elif isinstance(text, list):
            upstream_body["messages"] = text

    if "max_output_tokens" in upstream_body and "max_tokens" not in upstream_body:
        upstream_body["max_tokens"] = upstream_body.pop("max_output_tokens")

    upstream_body.pop("input", None)
    upstream_body.pop("prompt", None)
    upstream_body.pop("instructions", None)
    return upstream_body


def _openai_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "proxy_error",
                "code": status_code,
            }
        },
    )


def _extract_usage_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    return None


def _extract_error_text(payload: Any) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
    return ""


def _extract_response_model(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("model")
        if value:
            return str(value)
    return ""


def _display_model(request_model: str, actual_model: str) -> str:
    request_model = request_model or ""
    actual_model = actual_model or request_model
    if request_model and actual_model and request_model != actual_model:
        return f"{request_model}->{actual_model}"
    return actual_model or request_model or "-"


@app.get("/api/proxy/providers")
def api_proxy_providers():
    return {"providers": list_providers()}


@app.post("/api/proxy/providers")
def api_proxy_save_provider(body: ProxyProviderBody):
    upsert_provider(
        name=body.name.strip(),
        base_url=body.base_url.strip(),
        api_key=body.api_key.strip(),
        enabled=body.enabled,
        provider_id=body.id,
    )
    return {"ok": True, "providers": list_providers()}


@app.delete("/api/proxy/providers/{provider_id}")
def api_proxy_delete_provider(provider_id: int):
    delete_provider(provider_id)
    return {"ok": True, "providers": list_providers()}


@app.post("/api/proxy/providers/{provider_id}/toggle")
def api_proxy_toggle_provider(provider_id: int):
    return toggle_provider(provider_id)


@app.post("/api/proxy/providers/{provider_id}/test")
def api_proxy_test_provider(provider_id: int):
    provider = get_provider(provider_id)
    if not provider:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Provider not found"})
    status_code, payload, error = _read_upstream_json(_upstream_models_url(provider.base_url), provider)
    models = _extract_model_ids(payload)
    ok = 200 <= status_code < 300
    return {
        "ok": ok,
        "status_code": status_code,
        "message": "连接成功，API Key 可用" if ok else (error or "连接失败"),
        "models": models,
        "model_count": len(models),
    }


@app.get("/api/proxy/providers/{provider_id}/models")
def api_proxy_provider_models(provider_id: int):
    provider = get_provider(provider_id)
    if not provider:
        return JSONResponse(status_code=404, content={"ok": False, "error": "Provider not found", "models": []})
    status_code, payload, error = _read_upstream_json(_upstream_models_url(provider.base_url), provider)
    models = _extract_model_ids(payload)
    return {
        "ok": 200 <= status_code < 300,
        "status_code": status_code,
        "error": error,
        "models": models,
    }


@app.get("/api/proxy/mappings")
def api_proxy_mappings():
    return {"mappings": list_mappings()}


@app.post("/api/proxy/mappings")
def api_proxy_save_mapping(body: ProxyMappingBody):
    upsert_mapping(
        source_model=body.source_model.strip(),
        target_model=body.target_model.strip(),
        provider_id=body.provider_id,
        enabled=body.enabled,
        mapping_id=body.id,
    )
    return {"ok": True, "mappings": list_mappings()}


@app.delete("/api/proxy/mappings/{mapping_id}")
def api_proxy_delete_mapping(mapping_id: int):
    delete_mapping(mapping_id)
    return {"ok": True, "mappings": list_mappings()}


@app.post("/api/proxy/mappings/{mapping_id}/toggle")
def api_proxy_toggle_mapping(mapping_id: int):
    return toggle_mapping(mapping_id)


@app.get("/api/proxy/logs")
def api_proxy_logs(limit: int = Query(100, ge=1, le=500)):
    return {"items": proxy_log_rows(limit)}


@app.get("/api/proxy/status")
def api_proxy_status():
    return {"enabled": get_proxy_enabled()}


@app.post("/api/proxy/status")
def api_proxy_save_status(body: ProxyEnabledBody):
    set_proxy_enabled(body.enabled)
    # 代理开关同时切换所有 agent 配置
    _toggle_agent_configs(body.enabled)
    return {"ok": True, "enabled": get_proxy_enabled()}


@app.get("/api/proxy/active-mapping")
def api_proxy_get_active_mapping():
    return get_active_mapping()


@app.post("/api/proxy/active-mapping")
def api_proxy_set_active_mapping(body: ProxyActiveMappingBody):
    return {
        "ok": True,
        **set_active_mapping(
            target_model=body.target_model,
            provider_id=body.provider_id,
            mode=body.mode,
            mapping_id=body.mapping_id,
        ),
    }


@app.get("/api/proxy/last-mappings")
def api_proxy_last_mappings():
    """返回所有供应商的上次使用映射 {provider_id: mapping_id}。"""
    from hermes_token_dash.proxy_db import connect as _connect, get_last_mapping_id
    result = {}
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM proxy_providers").fetchall()
    for row in rows:
        mid = get_last_mapping_id(row["id"])
        if mid:
            result[str(row["id"])] = mid
    return {"last_mappings": result}


class ProxyLastMappingBody(BaseModel):
    provider_id: int
    mapping_id: int


@app.post("/api/proxy/last-mapping")
def api_proxy_set_last_mapping(body: ProxyLastMappingBody):
    from hermes_token_dash.proxy_db import set_last_mapping_id
    set_last_mapping_id(body.provider_id, body.mapping_id)
    return {"ok": True}


@app.get("/v1/models")
@app.get("/api/v1/models")
def proxy_models():
    """Hermes-compatible model list passthrough with a local fallback."""
    if not get_proxy_enabled():
        return _openai_error("Local proxy is disabled", 503)
    provider = _get_active_provider_for_metadata()
    if provider:
        return _upstream_get_json(_upstream_models_url(provider.base_url), provider, _model_list_fallback())
    return _model_list_fallback()


@app.get("/v1/models/{model_id:path}")
def proxy_model(model_id: str):
    """Hermes-compatible single-model probe passthrough."""
    if not get_proxy_enabled():
        return _openai_error("Local proxy is disabled", 503)
    provider = _get_active_provider_for_metadata()
    if provider:
        return _upstream_get_json(_upstream_model_url(provider.base_url, model_id), provider, _model_fallback(model_id))
    return _model_fallback(model_id)


@app.post("/api/show")
async def proxy_api_show(request: Request):
    """Ollama-style model probe used by Hermes before chat requests."""
    if not get_proxy_enabled():
        return _openai_error("Local proxy is disabled", 503)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
    except Exception:
        body = {}
    model_id = str(body.get("name") or body.get("model") or "")
    provider = _get_active_provider_for_metadata()
    if provider:
        return _upstream_post_json(_upstream_show_url(provider.base_url), provider, body, _show_fallback(model_id))
    return _show_fallback(model_id)


@app.post("/v1/chat/completions")
async def proxy_chat_completions(request: Request):
    """OpenAI-compatible chat completions proxy for Hermes."""
    if not get_proxy_enabled():
        return _openai_error("Local proxy is disabled", 503)
    try:
        body = await request.json()
    except Exception:
        return _openai_error("Request body must be JSON", 400)

    request_model = str(body.get("model") or "")
    auth_header = _request_auth_header(request)
    target_model, provider = _select_chat_provider(request_model, auth_header)
    if not provider:
        return _openai_error("No enabled proxy provider configured", 400)

    upstream_body = _normalize_chat_request_body(body, target_model)
    is_streaming = bool(upstream_body.get("stream"))
    if is_streaming:
        stream_options = dict(upstream_body.get("stream_options") or {})
        stream_options["include_usage"] = True
        upstream_body["stream_options"] = stream_options

    upstream_url = _upstream_chat_url(provider.base_url)
    headers = _provider_headers(
        provider,
        "text/event-stream" if is_streaming else "application/json",
    )
    created_at = int(time.time())
    start_ts = time.perf_counter()

    if is_streaming:
        return await _proxy_chat_stream(
            upstream_url,
            headers,
            upstream_body,
            provider,
            request_model,
            target_model,
            created_at,
            start_ts,
        )
    return await _proxy_chat_json(
        upstream_url,
        headers,
        upstream_body,
        provider,
        request_model,
        target_model,
        created_at,
        start_ts,
    )


async def _proxy_chat_json(
    upstream_url: str,
    headers: dict[str, str],
    upstream_body: dict[str, Any],
    provider,
    request_model: str,
    target_model: str,
    created_at: int,
    start_ts: float,
):
    import urllib.error
    import urllib.request

    request_id = str(uuid.uuid4())
    status_code = 502
    raw_usage = None
    error_message = ""
    content = b""
    media_type = "application/json"

    try:
        data = json.dumps(upstream_body).encode("utf-8")
        req = urllib.request.Request(upstream_url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=600) as resp:
            status_code = resp.status
            content = resp.read()
            media_type = resp.headers.get_content_type()
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        content = exc.read()
        media_type = exc.headers.get_content_type() if exc.headers else "application/json"
    except Exception as exc:
        error_message = str(exc)
        content = json.dumps({"error": {"message": error_message, "type": "proxy_error"}}).encode("utf-8")

    try:
        payload = json.loads(content.decode("utf-8"))
        request_id = payload.get("id") or request_id
        raw_usage = _extract_usage_from_payload(payload)
        response_model = _extract_response_model(payload)
        if status_code >= 400 and not error_message:
            error_message = _extract_error_text(payload)
    except Exception:
        response_model = ""
        if status_code >= 400 and not error_message:
            error_message = content.decode("utf-8", errors="ignore")[:1000]
    actual_model = response_model or target_model

    latency_ms = int((time.perf_counter() - start_ts) * 1000)
    insert_request_log(
        request_id=request_id,
        source_app="hermes",
        provider_id=provider.id,
        provider_name=provider.name,
        endpoint="/v1/chat/completions",
        request_model=request_model,
        model=actual_model,
        raw_usage=raw_usage,
        status_code=status_code,
        error_message=error_message,
        latency_ms=latency_ms,
        first_token_ms=latency_ms,
        is_streaming=False,
        usage_missing=raw_usage is None,
        created_at=created_at,
    )
    _load_cache()
    return Response(content=content, status_code=status_code, media_type=media_type)


async def _proxy_chat_stream(
    upstream_url: str,
    headers: dict[str, str],
    upstream_body: dict[str, Any],
    provider,
    request_model: str,
    target_model: str,
    created_at: int,
    start_ts: float,
):
    import urllib.error
    import urllib.request

    request_id = str(uuid.uuid4())
    raw_usage = None
    response_model = ""
    error_message = ""
    first_token_ms = 0
    upstream_resp = None

    try:
        data = json.dumps(upstream_body).encode("utf-8")
        req = urllib.request.Request(upstream_url, data=data, headers=headers, method="POST")
        upstream_resp = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as exc:
        content = exc.read()
        error_message = content.decode("utf-8", errors="ignore")[:1000]
        insert_request_log(
            request_id=request_id,
            source_app="hermes",
            provider_id=provider.id,
            provider_name=provider.name,
            endpoint="/v1/chat/completions",
            request_model=request_model,
            model=target_model,
            raw_usage=None,
            status_code=exc.code,
            error_message=error_message,
            latency_ms=int((time.perf_counter() - start_ts) * 1000),
            first_token_ms=0,
            is_streaming=True,
            usage_missing=True,
            created_at=created_at,
        )
        _load_cache()
        return Response(
            content=content,
            status_code=exc.code,
            media_type=exc.headers.get_content_type() if exc.headers else "application/json",
        )
    except Exception as exc:
        error_message = str(exc)
        insert_request_log(
            request_id=request_id,
            source_app="hermes",
            provider_id=provider.id,
            provider_name=provider.name,
            endpoint="/v1/chat/completions",
            request_model=request_model,
            model=target_model,
            raw_usage=None,
            status_code=502,
            error_message=error_message,
            latency_ms=int((time.perf_counter() - start_ts) * 1000),
            first_token_ms=0,
            is_streaming=True,
            usage_missing=True,
            created_at=created_at,
        )
        _load_cache()
        return _openai_error(error_message, 502)

    status_code = upstream_resp.status

    def body_iter():
        nonlocal request_id, raw_usage, error_message, first_token_ms
        try:
            while True:
                chunk = upstream_resp.read(8192)
                if not chunk:
                    break
                if chunk and first_token_ms <= 0:
                    first_token_ms = int((time.perf_counter() - start_ts) * 1000)
                _inspect_sse_chunk(
                    chunk,
                    lambda rid: _set_request_id(rid),
                    lambda usage: _set_usage(usage),
                    lambda model: _set_response_model(model),
                )
                yield chunk
        except GeneratorExit:
            error_message = "client_aborted"
            raise
        except Exception as exc:
            error_message = str(exc)
            raise
        finally:
            latency_ms = int((time.perf_counter() - start_ts) * 1000)
            try:
                upstream_resp.close()
            finally:
                insert_request_log(
                    request_id=request_id,
                    source_app="hermes",
                    provider_id=provider.id,
                    provider_name=provider.name,
                    endpoint="/v1/chat/completions",
                    request_model=request_model,
                    model=response_model or target_model,
                    raw_usage=raw_usage,
                    status_code=499 if error_message == "client_aborted" else status_code,
                    error_message=error_message,
                    latency_ms=latency_ms,
                    first_token_ms=first_token_ms,
                    is_streaming=True,
                    usage_missing=raw_usage is None,
                    created_at=created_at,
                )
                _load_cache()

    def _set_request_id(value: str):
        nonlocal request_id
        if value:
            request_id = value

    def _set_usage(value: dict[str, Any]):
        nonlocal raw_usage
        raw_usage = value

    def _set_response_model(value: str):
        nonlocal response_model
        if value:
            response_model = value

    return StreamingResponse(
        body_iter(),
        status_code=status_code,
        media_type=upstream_resp.headers.get_content_type() or "text/event-stream",
    )


def _inspect_sse_chunk(
    chunk: bytes,
    set_request_id,
    set_usage,
    set_response_model=None,
) -> None:
    try:
        text = chunk.decode("utf-8", errors="ignore")
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except Exception:
            continue
        if isinstance(payload, dict):
            if payload.get("id"):
                set_request_id(str(payload["id"]))
            if set_response_model and payload.get("model"):
                set_response_model(str(payload["model"]))
            usage = _extract_usage_from_payload(payload)
            if usage is not None:
                set_usage(usage)


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
        d["cost"] += _record_cost_cny(r)
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
        cost = round(_record_cost_cny(r), 6)
        items.append({
            "request_id": r.request_id,
            "model": r.model,
            "display_model": _display_model(getattr(r, "request_model", ""), r.model),
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
            "request_model": getattr(r, "request_model", ""),
            "endpoint": getattr(r, "endpoint", ""),
            "usage_missing": getattr(r, "usage_missing", False),
            "is_streaming": getattr(r, "is_streaming", False),
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
        d["total_cost"] += _record_cost_cny(r)

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
