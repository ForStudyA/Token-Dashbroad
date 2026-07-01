"""SQLite storage for Token Dashboard proxy traffic.

This database is the canonical source for usage statistics.  Historical
Claude/Codex/Hermes log parsers are intentionally not used by the web API.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_token_dash.models import EXCHANGE_RATE, TokenUsage, get_model_price


DATA_DIR = Path.home() / ".token-dashboard"
DB_PATH = DATA_DIR / "token-dashboard.db"


@dataclass
class ProviderConfig:
    id: int
    name: str
    base_url: str
    api_key: str
    enabled: bool


@dataclass
class ModelMapping:
    id: int
    source_model: str
    target_model: str
    provider_id: int
    provider_name: str
    enabled: bool


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    owns_conn = conn is None
    if conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
    try:
        # 每条 DDL 单独执行，避免 executescript 中某条失败导致全部跳过
        _DDL: list[str] = [
            """CREATE TABLE IF NOT EXISTS proxy_providers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS model_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_model TEXT NOT NULL,
                target_model TEXT NOT NULL,
                provider_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                protected INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY(provider_id) REFERENCES proxy_providers(id)
            )""",
            """CREATE TABLE IF NOT EXISTS proxy_request_logs (
                request_id TEXT PRIMARY KEY,
                source_app TEXT NOT NULL,
                provider_id INTEGER,
                provider_name TEXT NOT NULL DEFAULT '',
                endpoint TEXT NOT NULL,
                request_model TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                total_cost_cny REAL NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0,
                status_code INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                first_token_ms INTEGER NOT NULL DEFAULT 0,
                is_streaming INTEGER NOT NULL DEFAULT 0,
                is_estimated INTEGER NOT NULL DEFAULT 0,
                usage_missing INTEGER NOT NULL DEFAULT 0,
                raw_usage_json TEXT,
                created_at INTEGER NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS idx_proxy_logs_created ON proxy_request_logs(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_proxy_logs_model ON proxy_request_logs(model)",
            "CREATE INDEX IF NOT EXISTS idx_proxy_logs_source ON proxy_request_logs(source_app)",
            """CREATE TABLE IF NOT EXISTS proxy_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )""",
        ]
        for ddl in _DDL:
            try:
                conn.execute(ddl)
            except Exception:
                pass  # 表已存在或列已存在，忽略
        # 迁移：移除 model_mappings.source_model 的 UNIQUE 约束
        try:
            has_unique = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='unique' AND tbl_name='model_mappings'"
            ).fetchone()[0]
            if has_unique:
                conn.executescript("""
                    CREATE TABLE model_mappings_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_model TEXT NOT NULL,
                        target_model TEXT NOT NULL,
                        provider_id INTEGER NOT NULL,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY(provider_id) REFERENCES proxy_providers(id)
                    );
                    INSERT INTO model_mappings_new SELECT * FROM model_mappings;
                    DROP TABLE model_mappings;
                    ALTER TABLE model_mappings_new RENAME TO model_mappings;
                """)
        except Exception:
            pass
        # 迁移：添加 protected 列
        try:
            conn.execute("SELECT protected FROM model_mappings LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE model_mappings ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
        # 自动创建"原样转发"映射（如果不存在）
        try:
            default_p = conn.execute("SELECT id FROM proxy_providers WHERE enabled=1 LIMIT 1").fetchone()
            if default_p:
                exists = conn.execute(
                    "SELECT id FROM model_mappings WHERE source_model='*' AND target_model='*' AND protected=1"
                ).fetchone()
                if not exists:
                    ts = now_epoch()
                    conn.execute(
                        "INSERT INTO model_mappings (source_model, target_model, provider_id, enabled, protected, created_at, updated_at) VALUES (?, ?, ?, 0, 1, ?, ?)",
                        ("*", "*", default_p["id"], ts, ts),
                    )
        except Exception:
            pass
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


def now_epoch() -> int:
    return int(time.time())


def list_providers(include_key: bool = False) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT id, name, base_url, api_key, enabled, created_at, updated_at "
            "FROM proxy_providers ORDER BY id"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        if not include_key:
            item["api_key"] = mask_key(item["api_key"])
        returnable = item
        result.append(returnable)
    return result


def mask_key(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]


def get_proxy_enabled() -> bool:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT value FROM proxy_settings WHERE key = 'proxy_enabled'"
        ).fetchone()
    if row is None:
        return True
    return row["value"] not in {"0", "false", "False", "off", "OFF"}


def set_proxy_enabled(enabled: bool) -> dict[str, Any]:
    ts = now_epoch()
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO proxy_settings (key, value, updated_at)
            VALUES ('proxy_enabled', ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            ("1" if enabled else "0", ts),
        )
        conn.commit()
    return {"enabled": enabled}


def _get_setting(key: str) -> str:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT value FROM proxy_settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else ""


def _set_setting(key: str, value: str) -> None:
    ts = now_epoch()
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT INTO proxy_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, ts),
        )
        conn.commit()


def get_last_mapping_id(provider_id: int) -> int:
    """返回供应商上次使用的映射 ID，0 表示无记录。"""
    val = _get_setting(f"last_mapping_{provider_id}")
    return int(val) if val else 0


def set_last_mapping_id(provider_id: int, mapping_id: int) -> None:
    _set_setting(f"last_mapping_{provider_id}", str(mapping_id))


def get_default_model() -> str:
    """返回统一映射的目标模型名，空字符串表示不映射。"""
    return _get_setting("default_model")


def set_default_model(model: str) -> dict[str, Any]:
    _set_setting("default_model", model.strip())
    return {"default_model": model.strip()}


def get_active_mapping() -> dict[str, Any]:
    """Return the single active proxy configuration.

    ``mode`` is one of ``mapping``, ``passthrough``, or ``""``.  Legacy
    ``active_model``/``active_provider_id`` settings are still understood so
    existing installs do not lose their selected mapping after upgrade.
    """
    mode = _get_setting("active_proxy_mode")
    mapping_id = _get_setting("active_mapping_id")
    model = _get_setting("active_model")
    pid = _get_setting("active_provider_id")
    result = {
        "mode": mode,
        "target_model": model,
        "provider_id": int(pid) if pid else 0,
        "mapping_id": int(mapping_id) if mapping_id else 0,
    }

    if result["mode"] == "mapping" and result["mapping_id"]:
        with closing(connect()) as conn:
            row = conn.execute(
                """
                SELECT m.id, m.target_model, m.provider_id
                  FROM model_mappings m
                  JOIN proxy_providers p ON p.id = m.provider_id
                 WHERE m.id = ? AND m.protected = 0 AND p.enabled = 1
                 LIMIT 1
                """,
                (result["mapping_id"],),
            ).fetchone()
        if row:
            result.update(
                {
                    "target_model": row["target_model"],
                    "provider_id": int(row["provider_id"]),
                    "mapping_id": int(row["id"]),
                }
            )
            return result
        return _clear_active_proxy()

    if result["mode"] == "passthrough":
        provider = get_provider(result["provider_id"]) if result["provider_id"] else None
        if provider and provider.enabled:
            result["target_model"] = ""
            result["mapping_id"] = 0
            return result
        return _clear_active_proxy()

    if result["target_model"] and result["provider_id"]:
        # Legacy active mapping.  Try to attach the matching mapping row.
        with closing(connect()) as conn:
            row = conn.execute(
                """
                SELECT m.id
                  FROM model_mappings m
                  JOIN proxy_providers p ON p.id = m.provider_id
                 WHERE m.target_model = ? AND m.provider_id = ? AND m.protected = 0 AND p.enabled = 1
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (result["target_model"], result["provider_id"]),
            ).fetchone()
        result["mode"] = "mapping"
        result["mapping_id"] = int(row["id"]) if row else 0
        return result

    with closing(connect()) as conn:
        row = conn.execute(
            """
            SELECT provider_id
              FROM model_mappings
             WHERE protected = 1 AND enabled = 1
             ORDER BY id
             LIMIT 1
            """
        ).fetchone()
    provider = get_provider(int(row["provider_id"])) if row else None
    if provider and provider.enabled:
        return {
            "mode": "passthrough",
            "target_model": "",
            "provider_id": int(row["provider_id"]),
            "mapping_id": 0,
        }

    result["mode"] = ""
    result["target_model"] = ""
    result["provider_id"] = 0
    result["mapping_id"] = 0
    return result


def _write_active_proxy(
    mode: str,
    provider_id: int = 0,
    mapping_id: int = 0,
    target_model: str = "",
) -> dict[str, Any]:
    _set_setting("active_proxy_mode", mode)
    _set_setting("active_provider_id", str(provider_id) if provider_id else "")
    _set_setting("active_mapping_id", str(mapping_id) if mapping_id else "")
    _set_setting("active_model", target_model.strip())
    return {
        "mode": mode,
        "target_model": target_model.strip(),
        "provider_id": provider_id,
        "mapping_id": mapping_id,
    }


def _clear_active_proxy() -> dict[str, Any]:
    with closing(connect()) as conn:
        conn.execute("UPDATE model_mappings SET enabled = 0")
        conn.commit()
    return _write_active_proxy("")


def set_active_mapping(
    target_model: str = "",
    provider_id: int = 0,
    mode: str = "mapping",
    mapping_id: int = 0,
) -> dict[str, Any]:
    """Set the single active proxy.

    ``mapping`` mode activates one model mapping.  ``passthrough`` mode keeps
    the request model unchanged and routes to ``provider_id``.  Empty mode
    disables all concrete proxy routes.
    """
    mode = (mode or "").strip()
    target_model = target_model.strip()
    provider_id = int(provider_id or 0)
    mapping_id = int(mapping_id or 0)

    with closing(connect()) as conn:
        conn.execute("UPDATE model_mappings SET enabled = 0")
        if not mode:
            conn.commit()
            return _write_active_proxy("")

        if mode == "passthrough":
            provider = conn.execute(
                "SELECT id FROM proxy_providers WHERE id = ? AND enabled = 1", (provider_id,)
            ).fetchone()
            if not provider:
                conn.commit()
                return _write_active_proxy("")
            conn.commit()
            return _write_active_proxy("passthrough", provider_id=provider_id)

        if mode == "mapping":
            if mapping_id:
                row = conn.execute(
                    """
                    SELECT m.id, m.target_model, m.provider_id
                      FROM model_mappings m
                      JOIN proxy_providers p ON p.id = m.provider_id
                     WHERE m.id = ? AND m.protected = 0 AND p.enabled = 1
                     LIMIT 1
                    """,
                    (mapping_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT m.id, m.target_model, m.provider_id
                      FROM model_mappings m
                      JOIN proxy_providers p ON p.id = m.provider_id
                     WHERE m.target_model = ? AND m.provider_id = ? AND m.protected = 0 AND p.enabled = 1
                     ORDER BY id DESC
                     LIMIT 1
                    """,
                    (target_model, provider_id),
                ).fetchone()
            if not row:
                conn.commit()
                return _write_active_proxy("")
            conn.execute(
                "UPDATE model_mappings SET enabled = 1, updated_at = ? WHERE id = ?",
                (now_epoch(), row["id"]),
            )
            conn.commit()
            return _write_active_proxy(
                "mapping",
                provider_id=int(row["provider_id"]),
                mapping_id=int(row["id"]),
                target_model=row["target_model"],
            )

    return _write_active_proxy("")


def upsert_provider(
    name: str,
    base_url: str,
    api_key: str,
    enabled: bool = True,
    provider_id: int | None = None,
) -> dict[str, Any]:
    ts = now_epoch()
    with closing(connect()) as conn:
        if provider_id:
            old = conn.execute(
                "SELECT api_key FROM proxy_providers WHERE id = ?", (provider_id,)
            ).fetchone()
            key = api_key if api_key else (old["api_key"] if old else "")
            conn.execute(
                """
                UPDATE proxy_providers
                   SET name = ?, base_url = ?, api_key = ?, enabled = ?, updated_at = ?
                 WHERE id = ?
                """,
                (name, base_url, key, int(enabled), ts, provider_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO proxy_providers
                    (name, base_url, api_key, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    base_url = excluded.base_url,
                    api_key = CASE
                        WHEN excluded.api_key = '' THEN proxy_providers.api_key
                        ELSE excluded.api_key
                    END,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (name, base_url, api_key, int(enabled), ts, ts),
            )
        conn.commit()
    return {"ok": True}


def get_provider(provider_id: int) -> ProviderConfig | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT id, name, base_url, api_key, enabled FROM proxy_providers WHERE id = ?",
            (provider_id,),
        ).fetchone()
    return _provider_from_row(row) if row else None


def get_provider_by_name(name: str) -> ProviderConfig | None:
    with closing(connect()) as conn:
        row = conn.execute(
            """
            SELECT id, name, base_url, api_key, enabled
              FROM proxy_providers
             WHERE lower(name) = lower(?)
             LIMIT 1
            """,
            (name,),
        ).fetchone()
    return _provider_from_row(row) if row else None


def get_default_provider() -> ProviderConfig | None:
    with closing(connect()) as conn:
        row = conn.execute(
            """
            SELECT id, name, base_url, api_key, enabled
              FROM proxy_providers
             WHERE enabled = 1
             ORDER BY id
             LIMIT 1
            """
        ).fetchone()
    return _provider_from_row(row) if row else None


def _provider_from_row(row: sqlite3.Row) -> ProviderConfig:
    return ProviderConfig(
        id=int(row["id"]),
        name=row["name"],
        base_url=row["base_url"],
        api_key=row["api_key"],
        enabled=bool(row["enabled"]),
    )


def delete_provider(provider_id: int) -> dict[str, Any]:
    active = get_active_mapping()
    with closing(connect()) as conn:
        conn.execute("DELETE FROM model_mappings WHERE provider_id = ?", (provider_id,))
        conn.execute("DELETE FROM proxy_providers WHERE id = ?", (provider_id,))
        conn.commit()
    if active["provider_id"] == provider_id:
        _clear_active_proxy()
    return {"ok": True}


def toggle_provider(provider_id: int) -> dict[str, Any]:
    active = get_active_mapping()
    ts = now_epoch()
    with closing(connect()) as conn:
        row = conn.execute("SELECT enabled FROM proxy_providers WHERE id = ?", (provider_id,)).fetchone()
        if not row:
            return {"ok": False, "error": "Provider not found"}
        new_val = 0 if row["enabled"] else 1
        conn.execute("UPDATE proxy_providers SET enabled = ?, updated_at = ? WHERE id = ?", (new_val, ts, provider_id))
        conn.commit()
    if not new_val and active["provider_id"] == provider_id:
        _clear_active_proxy()
    return {"ok": True, "enabled": bool(new_val)}


def toggle_mapping(mapping_id: int) -> dict[str, Any]:
    row = None
    with closing(connect()) as conn:
        row = conn.execute(
            """
            SELECT id, target_model, provider_id, enabled, protected
              FROM model_mappings
             WHERE id = ?
            """,
            (mapping_id,),
        ).fetchone()
    if not row:
        return {"ok": False, "error": "Mapping not found"}
    if row["protected"]:
        return set_active_mapping(mode="passthrough", provider_id=int(row["provider_id"]))
    if row["enabled"]:
        return {"ok": True, **_clear_active_proxy()}
    return {"ok": True, **set_active_mapping(mode="mapping", mapping_id=int(row["id"]))}


def delete_mapping(mapping_id: int) -> dict[str, Any]:
    active = get_active_mapping()
    with closing(connect()) as conn:
        row = conn.execute("SELECT protected FROM model_mappings WHERE id = ?", (mapping_id,)).fetchone()
        if row and row["protected"]:
            return {"ok": False, "error": "Cannot delete protected mapping"}
        conn.execute("DELETE FROM model_mappings WHERE id = ?", (mapping_id,))
        conn.commit()
    if active["mapping_id"] == mapping_id:
        _clear_active_proxy()
    return {"ok": True}


def list_mappings() -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT m.id, m.source_model, m.target_model, m.provider_id,
                   p.name AS provider_name, m.enabled, m.protected, m.created_at, m.updated_at
              FROM model_mappings m
              JOIN proxy_providers p ON p.id = m.provider_id
             ORDER BY m.protected DESC, m.source_model
            """
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["protected"] = bool(item["protected"])
        result.append(item)
    return result


def upsert_mapping(
    source_model: str,
    target_model: str,
    provider_id: int,
    enabled: bool = True,
    mapping_id: int | None = None,
) -> dict[str, Any]:
    ts = now_epoch()
    with closing(connect()) as conn:
        if mapping_id:
            old = conn.execute(
                "SELECT enabled FROM model_mappings WHERE id = ?", (mapping_id,)
            ).fetchone()
            current_enabled = int(old["enabled"]) if old else 0
            conn.execute(
                """
                UPDATE model_mappings
                   SET source_model = ?, target_model = ?, provider_id = ?,
                       enabled = ?, updated_at = ?
                 WHERE id = ?
                """,
                (source_model, target_model, provider_id, current_enabled, ts, mapping_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO model_mappings
                    (source_model, target_model, provider_id, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_model, target_model, provider_id, 0, ts, ts),
            )
        conn.commit()
    return {"ok": True}


def resolve_mapping(request_model: str) -> tuple[str, ProviderConfig] | None:
    active = get_active_mapping()
    provider_id = int(active.get("provider_id") or 0)
    if not active.get("mode") or not provider_id:
        return None
    provider = get_provider(provider_id)
    if not provider:
        return None
    if active["mode"] == "passthrough":
        return request_model, provider
    if active["mode"] == "mapping" and active.get("target_model"):
        return str(active["target_model"]), provider
    return None


def normalize_usage(raw_usage: dict[str, Any] | None) -> dict[str, int]:
    usage = raw_usage or {}
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    cache_read = (
        usage.get("cache_read_input_tokens")
        or usage.get("cache_read_tokens")
        or usage.get("cached_input_tokens")
        or details.get("cached_tokens")
        or details.get("cached_input_tokens")
        or 0
    )
    cache_creation = (
        usage.get("cache_creation_input_tokens")
        or usage.get("cache_creation_tokens")
        or 0
    )
    has_prompt_tokens = usage.get("prompt_tokens") is not None
    input_tokens = (
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("total_input_tokens")
        or 0
    )
    output_tokens = (
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("total_output_tokens")
        or 0
    )
    reasoning = (
        usage.get("reasoning_tokens")
        or completion_details.get("reasoning_tokens")
        or 0
    )
    input_tokens = int(input_tokens or 0)
    cache_read = int(cache_read or 0)
    if has_prompt_tokens and cache_read > 0:
        input_tokens = max(0, input_tokens - cache_read)
    return {
        "input_tokens": input_tokens,
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": int(cache_creation or 0),
        "reasoning_tokens": int(reasoning or 0),
    }


def compute_cost_cny(model: str, usage: dict[str, int]) -> float:
    in_price, out_price, cr_price = get_model_price(model)
    total_input = usage["input_tokens"] + usage["cache_read_tokens"]
    non_cache = max(0, total_input - usage["cache_read_tokens"])
    return (
        non_cache / 1_000_000 * in_price
        + usage["cache_read_tokens"] / 1_000_000 * cr_price
        + (usage["output_tokens"] + usage["reasoning_tokens"]) / 1_000_000 * out_price
    )


def insert_request_log(**values: Any) -> str:
    request_id = values.get("request_id") or str(uuid.uuid4())
    usage = normalize_usage(values.get("raw_usage"))
    model = values.get("model") or values.get("request_model") or ""
    total_cost_cny = values.get("total_cost_cny")
    if total_cost_cny is None:
        total_cost_cny = compute_cost_cny(model, usage)
    raw_usage_json = None
    if values.get("raw_usage") is not None:
        raw_usage_json = json.dumps(values["raw_usage"], ensure_ascii=False)
    with closing(connect()) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO proxy_request_logs (
                request_id, source_app, provider_id, provider_name, endpoint,
                request_model, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, reasoning_tokens,
                total_cost_cny, total_cost_usd, status_code, error_message,
                latency_ms, first_token_ms, is_streaming, is_estimated,
                usage_missing, raw_usage_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                values.get("source_app", "hermes"),
                values.get("provider_id"),
                values.get("provider_name", ""),
                values.get("endpoint", ""),
                values.get("request_model", ""),
                model,
                usage["input_tokens"],
                usage["output_tokens"],
                usage["cache_read_tokens"],
                usage["cache_creation_tokens"],
                usage["reasoning_tokens"],
                float(total_cost_cny or 0),
                float(values.get("total_cost_usd") or 0),
                int(values.get("status_code") or 0),
                values.get("error_message"),
                int(values.get("latency_ms") or 0),
                int(values.get("first_token_ms") or 0),
                int(bool(values.get("is_streaming"))),
                int(bool(values.get("is_estimated"))),
                int(bool(values.get("usage_missing"))),
                raw_usage_json,
                int(values.get("created_at") or now_epoch()),
            ),
        )
        conn.commit()
    return request_id


def parse_proxy_request_logs() -> list[TokenUsage]:
    init_db()
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM proxy_request_logs
             ORDER BY created_at
            """
        ).fetchall()
    records: list[TokenUsage] = []
    for row in rows:
        total_input = int(row["input_tokens"] or 0) + int(row["cache_read_tokens"] or 0)
        rec = TokenUsage(
            request_id=row["request_id"],
            model=row["model"] or row["request_model"] or "unknown",
            input_tokens=total_input,
            output_tokens=int(row["output_tokens"] or 0),
            cache_read=int(row["cache_read_tokens"] or 0),
            cache_creation=int(row["cache_creation_tokens"] or 0),
            reasoning_tokens=int(row["reasoning_tokens"] or 0),
            timestamp=datetime.fromtimestamp(int(row["created_at"] or 0), timezone.utc),
            data_source=row["source_app"] or "proxy",
            status_code=int(row["status_code"] or 0),
            latency_ms=float(row["latency_ms"] or 0),
            first_token_ms=float(row["first_token_ms"] or 0),
            agent=row["provider_name"] or "",
        )
        rec.total_cost_cny = float(row["total_cost_cny"] or 0)
        rec.total_cost_usd = float(row["total_cost_usd"] or 0)
        rec.request_model = row["request_model"] or ""
        rec.endpoint = row["endpoint"] or ""
        rec.usage_missing = bool(row["usage_missing"])
        rec.is_streaming = bool(row["is_streaming"])
        records.append(rec)
    return records


def proxy_log_rows(limit: int = 100) -> list[dict[str, Any]]:
    with closing(connect()) as conn:
        rows = conn.execute(
            """
            SELECT *
              FROM proxy_request_logs
             ORDER BY created_at DESC
             LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        request_model = item.get("request_model") or ""
        actual_model = item.get("model") or request_model
        item["display_model"] = (
            f"{request_model}->{actual_model}"
            if request_model and actual_model and request_model != actual_model
            else (actual_model or request_model or "-")
        )
        item["created_at_iso"] = datetime.fromtimestamp(
            int(row["created_at"] or 0), timezone.utc
        ).isoformat()
        result.append(item)
    return result
