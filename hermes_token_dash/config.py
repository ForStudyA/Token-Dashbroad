"""Configuration constants for Hermes Token Dashboard.

Centralizes paths, defaults, and settings that may vary by environment.
"""

from __future__ import annotations

from pathlib import Path

# ── Data sources ──────────────────────────────────────────────

HERMES_HOME = Path.home() / "AppData" / "Local" / "hermes"
HERMES_MAIN_DB = HERMES_HOME / "state.db"
HERMES_PROFILES_DIR = HERMES_HOME / "profiles"
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# ── Server defaults ───────────────────────────────────────────

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
AUTO_REFRESH_INTERVAL = 5  # seconds

# ── Time filter options ───────────────────────────────────────

TIME_FILTERS = {
    "all": "All Time",
    "today": "Today",
    "7d": "Last 7 Days",
    "30d": "Last 30 Days",
}

# ── Log limits ────────────────────────────────────────────────

MAX_LOG_LIMIT = 500
DEFAULT_LOG_LIMIT = 50

# ── Display ───────────────────────────────────────────────────

# Model display name mapping (friendly names for long model IDs)
MODEL_DISPLAY_NAMES: dict[str, str] = {
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "mimo-v2.5": "MiMo V2.5",
    "mimo-v2.5-pro": "MiMo V2.5 Pro",
    "claude-sonnet-4-6": "Claude Sonnet 4",
    "claude-opus-4-8": "Claude Opus 4",
}
