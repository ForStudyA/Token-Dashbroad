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
from pathlib import Path
from typing import Any

import yaml

from hermes_token_dash.adapters.base import AgentAdapter

HERMES_HOME = Path.home() / ".hermes"
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
ORIGINALS_PATH = HERMES_HOME / ".proxy_originals.json"

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
        return CONFIG_PATH

    def is_installed(self) -> bool:
        return CONFIG_PATH.exists()

    def get_current_base_url(self) -> str | None:
        """Read the active base URL from config."""
        try:
            cfg = self._read_config()
        except Exception:
            return None
        # Check custom_providers first
        for prov in cfg.get("custom_providers", []):
            if isinstance(prov, dict) and prov.get("base_url"):
                return prov["base_url"]
        # Check .env for built-in provider overrides
        env_url = self._read_env_var("DEEPSEEK_BASE_URL") or self._read_env_var("XIAOMI_BASE_URL")
        return env_url

    def set_proxy_url(self, proxy_url: str) -> bool:
        """Set all providers to route through proxy."""
        try:
            self._save_originals()
            self._modify_custom_providers(proxy_url)
            self._modify_model_base_url(proxy_url)
            self._set_builtin_env_vars(proxy_url)
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
        cfg = self._read_config()
        model = cfg.get("model") or {}
        if isinstance(model, dict):
            model["base_url"] = proxy_url
            cfg["model"] = model
        self._write_config(cfg)

    def _restore_model_base_url(self) -> None:
        """Restore model.base_url to original."""
        original = self._originals.get("model_base_url")
        cfg = self._read_config()
        model = cfg.get("model") or {}
        if isinstance(model, dict):
            if original:
                model["base_url"] = original
            elif "base_url" in model:
                del model["base_url"]
            cfg["model"] = model
        self._write_config(cfg)

    # ── Config read/write ──────────────────────────────────────

    def _read_config(self) -> dict[str, Any]:
        if not CONFIG_PATH.exists():
            return {}
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _write_config(self, cfg: dict[str, Any]) -> None:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

    # ── Custom providers ───────────────────────────────────────

    def _save_originals(self) -> None:
        """Save original base_urls for all providers."""
        cfg = self._read_config()
        originals: dict[str, Any] = {"custom_providers": {}}
        for prov in cfg.get("custom_providers", []):
            if isinstance(prov, dict) and prov.get("name"):
                originals["custom_providers"][prov["name"]] = prov.get("base_url", "")
        # Save model.base_url
        model = cfg.get("model") or {}
        if isinstance(model, dict) and model.get("base_url"):
            originals["model_base_url"] = model["base_url"]
        # Save env var originals
        for provider_name, env_var in BUILTIN_PROVIDERS.items():
            val = self._read_env_var(env_var)
            if val:
                originals[f"env_{provider_name}"] = val
        # Persist to file
        ORIGINALS_PATH.write_text(json.dumps(originals, indent=2), encoding="utf-8")
        self._originals = originals

    def _modify_custom_providers(self, proxy_url: str) -> None:
        """Set all custom_providers base_url to proxy."""
        cfg = self._read_config()
        for prov in cfg.get("custom_providers", []):
            if isinstance(prov, dict):
                prov["base_url"] = proxy_url
        self._write_config(cfg)

    def _restore_custom_providers(self) -> None:
        """Restore custom_providers to original base_urls."""
        originals = self._originals.get("custom_providers", {})
        if not originals:
            return
        cfg = self._read_config()
        for prov in cfg.get("custom_providers", []):
            if isinstance(prov, dict) and prov.get("name") in originals:
                prov["base_url"] = originals[prov["name"]]
        self._write_config(cfg)

    # ── Built-in provider env vars ─────────────────────────────

    def _set_builtin_env_vars(self, proxy_url: str) -> None:
        """Set DEEPSEEK_BASE_URL / XIAOMI_BASE_URL in .env file."""
        for provider_name, env_var in BUILTIN_PROVIDERS.items():
            self._set_env_var(env_var, proxy_url)

    def _restore_builtin_env_vars(self) -> None:
        """Remove built-in provider env vars from .env file."""
        for provider_name, env_var in BUILTIN_PROVIDERS.items():
            if f"env_{provider_name}" in self._originals:
                original = self._originals[f"env_{provider_name}"]
                self._set_env_var(env_var, original)
            else:
                self._remove_env_var(env_var)

    # ── .env file manipulation ─────────────────────────────────

    def _read_env_var(self, key: str) -> str | None:
        """Read a specific env var from .env file."""
        if not ENV_PATH.exists():
            return None
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip("'\"")
        return None

    def _set_env_var(self, key: str, value: str) -> None:
        """Set an env var in .env file (create or update)."""
        lines: list[str] = []
        found = False
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
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
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _remove_env_var(self, key: str) -> None:
        """Remove an env var from .env file."""
        if not ENV_PATH.exists():
            return
        lines = []
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k != key:
                lines.append(line)
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
