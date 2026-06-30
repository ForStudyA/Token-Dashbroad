"""Hermes Agent config adapter.

Handles two types of providers:
1. Custom providers (in config.yaml custom_providers) — modify base_url directly
2. Built-in providers (deepseek, xiaomi) — set env vars in .env file

Env vars are read at Hermes startup, so changes require Hermes restart.
Original values are persisted to a JSON file for restore across instances.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from hermes_token_dash.adapters.base import AgentAdapter

HERMES_HOMES = [
    Path.home() / ".hermes",
    Path.home() / "AppData" / "Local" / "hermes",
]
ORIGINALS_PATH = Path.home() / ".token-dashboard" / "hermes_proxy_originals.json"

# Built-in providers that use env vars for base_url override
BUILTIN_PROVIDERS: dict[str, str] = {
    "deepseek": "DEEPSEEK_BASE_URL",
    "xiaomi": "XIAOMI_BASE_URL",
}


class HermesAdapter(AgentAdapter):

    def __init__(self) -> None:
        self._originals: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "hermes"

    @property
    def display_name(self) -> str:
        return "Hermes Agent"

    @property
    def config_path(self) -> Path:
        config_paths = self._config_paths()
        return next((path for path in config_paths if path.exists()), HERMES_HOMES[0] / "config.yaml")

    def is_installed(self) -> bool:
        return bool(self._config_paths())

    def get_current_base_url(self) -> str | None:
        """Read the active base URL from config."""
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            try:
                cfg = self._read_config(config_path)
            except Exception:
                continue
            for prov in cfg.get("custom_providers", []):
                if isinstance(prov, dict) and prov.get("base_url"):
                    return prov["base_url"]
            model = cfg.get("model") or {}
            if isinstance(model, dict) and model.get("base_url"):
                return model["base_url"]
            home = config_path.parent
            env_url = (
                self._read_env_var(home, "DEEPSEEK_BASE_URL")
                or self._read_env_var(home, "XIAOMI_BASE_URL")
            )
            if env_url:
                return env_url
        return None

    def set_proxy_url(self, proxy_url: str) -> bool:
        """Set all providers to route through proxy."""
        try:
            self._save_originals()
            self._modify_custom_providers(proxy_url)
            self._modify_model_base_url(proxy_url)
            self._set_builtin_env_vars(proxy_url)
            self._modify_state_sessions(proxy_url)
            return True
        except Exception:
            return False

    def restore_original(self) -> bool:
        """Restore all providers to their original URLs."""
        try:
            # Load originals from file if not in memory
            if not self._originals and ORIGINALS_PATH.exists():
                self._originals = json.loads(ORIGINALS_PATH.read_text(encoding="utf-8"))
            self._restore_custom_providers()
            self._restore_model_base_url()
            self._restore_builtin_env_vars()
            self._restore_state_sessions()
            # Clean up
            self._originals.clear()
            if ORIGINALS_PATH.exists():
                ORIGINALS_PATH.unlink()
            return True
        except Exception:
            return False

    # ── Model base_url ─────────────────────────────────────────

    def _modify_model_base_url(self, proxy_url: str) -> None:
        """Set model.base_url to proxy (this is what Hermes actually reads)."""
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            cfg = self._read_config(config_path)
            model = cfg.get("model") or {}
            if isinstance(model, dict):
                model["base_url"] = proxy_url
                cfg["model"] = model
            self._write_config(config_path, cfg)

    def _restore_model_base_url(self) -> None:
        """Restore model.base_url to original."""
        configs = self._originals.get("configs", {})
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            original = configs.get(str(config_path), {}).get("model_base_url")
            cfg = self._read_config(config_path)
            model = cfg.get("model") or {}
            if isinstance(model, dict):
                if original:
                    model["base_url"] = original
                elif "base_url" in model:
                    del model["base_url"]
                cfg["model"] = model
            self._write_config(config_path, cfg)

    # ── Config read/write ──────────────────────────────────────

    def _read_config(self, config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            return {}
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write_config(self, config_path: Path, cfg: dict[str, Any]) -> None:
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

    # 鈹€鈹€ Config discovery 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _config_paths(self) -> list[Path]:
        """Return root and profile configs Hermes may load."""
        paths: list[Path] = []
        seen: set[Path] = set()
        for home in HERMES_HOMES:
            candidates = [home / "config.yaml"]
            profiles_dir = home / "profiles"
            if profiles_dir.is_dir():
                candidates.extend(sorted(profiles_dir.glob("*/config.yaml")))
            for path in candidates:
                if path.exists() and path not in seen:
                    paths.append(path)
                    seen.add(path)
        return paths

    def _config_homes(self) -> list[Path]:
        """Return directories that contain a Hermes config.yaml."""
        return [path.parent for path in self._config_paths()]

    def _state_db_paths(self) -> list[Path]:
        """Return Hermes state DBs that can persist session base URLs."""
        paths: list[Path] = []
        seen: set[Path] = set()
        for home in HERMES_HOMES:
            candidates = [home / "state.db"]
            profiles_dir = home / "profiles"
            if profiles_dir.is_dir():
                candidates.extend(sorted(profiles_dir.glob("*/state.db")))
            for path in candidates:
                if path.exists() and path not in seen:
                    paths.append(path)
                    seen.add(path)
        return paths

    # ── Custom providers ───────────────────────────────────────

    def _save_originals(self) -> None:
        """Save original base_urls for all providers."""
        originals: dict[str, Any] = {"configs": {}, "env": {}, "state_dbs": {}}
        if ORIGINALS_PATH.exists():
            try:
                loaded = json.loads(ORIGINALS_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    originals["configs"] = dict(loaded.get("configs") or {})
                    originals["env"] = dict(loaded.get("env") or {})
                    originals["state_dbs"] = dict(loaded.get("state_dbs") or {})
            except Exception:
                pass
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            cfg = self._read_config(config_path)
            existing_config = originals["configs"].get(str(config_path), {})
            config_originals: dict[str, Any] = {
                "custom_providers": dict(existing_config.get("custom_providers") or {})
            }
            for prov in cfg.get("custom_providers", []):
                if isinstance(prov, dict) and prov.get("name"):
                    if prov["name"] not in config_originals["custom_providers"]:
                        config_originals["custom_providers"][prov["name"]] = prov.get("base_url", "")
            model = cfg.get("model") or {}
            if existing_config.get("model_base_url"):
                config_originals["model_base_url"] = existing_config["model_base_url"]
            elif isinstance(model, dict) and model.get("base_url"):
                config_originals["model_base_url"] = model["base_url"]
            originals["configs"][str(config_path)] = config_originals

            env_path_key = str(config_path.parent / ".env")
            env_originals: dict[str, str] = dict(originals["env"].get(env_path_key) or {})
            for provider_name, env_var in BUILTIN_PROVIDERS.items():
                val = self._read_env_var(config_path.parent, env_var)
                if val and provider_name not in env_originals:
                    env_originals[provider_name] = val
            originals["env"][env_path_key] = env_originals

        for db_path in self._state_db_paths():
            existing_rows = dict(originals["state_dbs"].get(str(db_path), {}) or {})
            for rowid, base_url in self._read_state_session_base_urls(db_path).items():
                existing_rows.setdefault(rowid, base_url)
            originals["state_dbs"][str(db_path)] = existing_rows

        ORIGINALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ORIGINALS_PATH.write_text(json.dumps(originals, indent=2), encoding="utf-8")
        self._originals = originals

    def _modify_custom_providers(self, proxy_url: str) -> None:
        """Set all custom_providers base_url to proxy."""
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            cfg = self._read_config(config_path)
            for prov in cfg.get("custom_providers", []):
                if isinstance(prov, dict):
                    prov["base_url"] = proxy_url
            self._write_config(config_path, cfg)

    def _restore_custom_providers(self) -> None:
        """Restore custom_providers to original base_urls."""
        configs = self._originals.get("configs", {})
        for config_path in self._config_paths():
            if not config_path.exists():
                continue
            originals = configs.get(str(config_path), {}).get("custom_providers", {})
            if not originals:
                continue
            cfg = self._read_config(config_path)
            for prov in cfg.get("custom_providers", []):
                if isinstance(prov, dict) and prov.get("name") in originals:
                    prov["base_url"] = originals[prov["name"]]
            self._write_config(config_path, cfg)

    # ── Built-in provider env vars ─────────────────────────────

    def _set_builtin_env_vars(self, proxy_url: str) -> None:
        """Set DEEPSEEK_BASE_URL / XIAOMI_BASE_URL in .env file."""
        for home in self._config_homes():
            for provider_name, env_var in BUILTIN_PROVIDERS.items():
                self._set_env_var(home, env_var, proxy_url)

    def _restore_builtin_env_vars(self) -> None:
        """Remove built-in provider env vars from .env file."""
        env_originals = self._originals.get("env", {})
        for home in self._config_homes():
            originals = env_originals.get(str(home / ".env"), {})
            for provider_name, env_var in BUILTIN_PROVIDERS.items():
                if provider_name in originals:
                    self._set_env_var(home, env_var, originals[provider_name])
                else:
                    self._remove_env_var(home, env_var)

    # ── .env file manipulation ─────────────────────────────────

    def _read_env_var(self, home: Path, key: str) -> str | None:
        """Read a specific env var from .env file."""
        env_path = home / ".env"
        if not env_path.exists():
            return None
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
        return None

    def _set_env_var(self, home: Path, key: str, value: str) -> None:
        """Set an env var in .env file (create or update)."""
        env_path = home / ".env"
        lines: list[str] = []
        found = False
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "=" not in stripped:
                    lines.append(line)
                    continue
                k = stripped.split("=", 1)[0].strip()
                if k == key:
                    lines.append(f"{key}={value}")
                    found = True
                else:
                    lines.append(line)
        if not found:
            lines.append(f"{key}={value}")
        home.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 鈹€鈹€ Hermes state DB session URLs 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

    def _read_state_session_base_urls(self, db_path: Path) -> dict[str, str]:
        """Read session billing_base_url values keyed by sqlite rowid."""
        try:
            with sqlite3.connect(db_path) as con:
                if not self._state_db_has_sessions_base_url(con):
                    return {}
                rows = con.execute(
                    "SELECT rowid, billing_base_url FROM sessions "
                    "WHERE billing_base_url IS NOT NULL AND billing_base_url != ''"
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {str(rowid): base_url for rowid, base_url in rows}

    def _modify_state_sessions(self, proxy_url: str) -> None:
        """Point persisted Hermes sessions at the proxy too."""
        for db_path in self._state_db_paths():
            try:
                with sqlite3.connect(db_path) as con:
                    if not self._state_db_has_sessions_base_url(con):
                        continue
                    con.execute(
                        "UPDATE sessions SET billing_base_url = ? "
                        "WHERE billing_base_url IS NOT NULL AND billing_base_url != ''",
                        (proxy_url,),
                    )
                    con.commit()
            except sqlite3.Error:
                continue

    def _restore_state_sessions(self) -> None:
        """Restore persisted Hermes session base URLs."""
        state_dbs = self._originals.get("state_dbs", {})
        for db_path in self._state_db_paths():
            originals = state_dbs.get(str(db_path), {})
            if not originals:
                continue
            try:
                with sqlite3.connect(db_path) as con:
                    if not self._state_db_has_sessions_base_url(con):
                        continue
                    for rowid, base_url in originals.items():
                        con.execute(
                            "UPDATE sessions SET billing_base_url = ? WHERE rowid = ?",
                            (base_url, rowid),
                        )
                    con.commit()
            except sqlite3.Error:
                continue

    def _state_db_has_sessions_base_url(self, con: sqlite3.Connection) -> bool:
        tables = {
            row[0]
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "sessions" not in tables:
            return False
        columns = {row[1] for row in con.execute("PRAGMA table_info(sessions)").fetchall()}
        return "billing_base_url" in columns

    def _remove_env_var(self, home: Path, key: str) -> None:
        """Remove an env var from .env file."""
        env_path = home / ".env"
        if not env_path.exists():
            return
        lines = []
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k != key:
                lines.append(line)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
