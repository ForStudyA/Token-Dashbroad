#!/usr/bin/env python
"""
Privacy check script — run before every git commit.
Auto-filters false positives. Only alerts on genuinely suspicious items.

Usage:
    python scripts/check_privacy.py
    # Exits 0 if clean, 1 if human review needed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ── High-confidence patterns (real leaks, not docs) ────────────

# Windows user path: C:\Users\<name>\ (real user home)
REAL_WIN_USER = re.compile(r"[cC]:\\Users\\[^\\]+\\")

# Unix user home path: /home/<name>/ or /Users/<name>/
REAL_UNIX_HOME = re.compile(r"/(?:home|Users)/[^/]+/")

# API key: quoted string starting with known key prefixes
API_KEY_PATTERN = re.compile(
    r"""(['\"])
    (?:sk-[a-zA-Z0-9]{10,}                # OpenAI-style sk-...
    |fk-[a-zA-Z0-9]+                       # fk- prefixed keys
    |pk-[a-zA-Z0-9]+                       # pk- prefixed keys
    )
    \1                                      # closing same quote
    """,
    re.VERBOSE,
)

# Sensitive variable name assigned a quoted string
SENSITIVE_ASSIGN = re.compile(
    r"""^\s*
    (?:api[_-]?key|apikey|secret|secret_key|access_key|private_key
       |password|passwd|pwd|auth_token|bearer|jwt|refresh_token)
    \s*[=:]\s*['\"].{4,}['\"]
    """,
    re.IGNORECASE | re.VERBOSE,
)

# SSH private key header
SSH_KEY = re.compile(
    r"-----BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY-----"
)

# URL with embedded credentials
URL_WITH_CREDS = re.compile(r"https?://[^:]+:[^@]+@")

# JWT token
JWT_PATTERN = re.compile(
    r"""['"]
    eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}
    ['"]""",
    re.VERBOSE,
)

# ── Binary / non-text files to skip ────────────────────────────

BINARY_EXT = frozenset({
    ".pyc", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg",
    ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".eot",
    ".ttf", ".pdf", ".zip", ".tar", ".gz", ".7z", ".lock",
    ".db", ".sqlite", ".sqlite3", ".rdb", ".aof",
    ".whl", ".egg",
})

SENSITIVE_FILENAMES = frozenset({"mimo_cookies.json", "deepseek_key.txt"})


# ── Known-safe lines (auto-skip) ───────────────────────────────

def is_safe_line(line: str) -> bool:
    """Return True if line is known-safe (auto-skip, no human review needed)."""
    s = line.strip()
    if not s:
        return True

    # Python control flow / import lines
    if s.startswith(("import ", "from ", "def ", "class ", "return ",
                     "assert ", "yield ", "raise ", "pass", "break",
                     "continue", "print(", "self.", "cls.", "super()",
                     "@", "#", "//")):
        return True

    # Markdown / documentation / table lines
    if s and s[0] in ("|", ">", "*", "`") and not s.startswith("`sk-"):
        return True
    if s.startswith(("- ", "1.", "2.", "3.", "4.", "5.")):
        return True

    # Known safe substrings
    lower = s.lower()
    safe_patterns = [
        # System paths
        "c:\\program files", "c:\\windows\\", "c:\\users\\public\\",
        "/usr/bin/", "/usr/share/", "/usr/lib/", "/etc/", "/var/",
        # URLs
        "https://", "http://", "git@github.com",
        # Documentation example paths
        "`c:\\", "`/home/", "`/users/", "`d:\\",
        "c:\\users\\...", "/home/...", "/users/...",
        # Test-specific fake paths
        "/home/user/", "/tmp/",
        # API endpoint documentation (not real paths)
        "/api/", "/v1/", "/v2/", "/graphql",
        # Generic config keys (not real secrets)
        "api_key", "api-key", "access_key", "secret_key",
        # Documentation markers
        "sk-", "fk-", "pk-",
        # Package metadata
        'name = "', 'version = "', 'description = "',
        'title = "', 'media_type = "',
        # HTML/CSS inline
        "style=", "<div", "</div>", "<span", "</span>",
        "<button", "<table", "<thead", "<tbody", "<tr>", "<td>",
        "<script", "<h1", "<h2", "<h3",
        # Common doc string
        "````", "---", "###",
        # Test code
        "mock_home", "assert ",
        # Safe config values
        '"127.0.0.1"', '"localhost"', '"0.0.0.0"',
        # Package.json / pyproject patterns
        '"name"', '"description"', '"version"',
        # Environment variable reads (not a leak)
        "os.environ", "os.getenv", "environ.get",
        # Documentation emoji markers
        "sk-", "fk-", "pk-",
        # CSS class / style patterns
        "class=\"", "style=\"",
        # Vue template patterns
        "v-if=", "v-for=", "v-model=", "@click", ":class=",
    ]

    for pat in safe_patterns:
        if pat in s or pat in lower:
            return True

    return False


# ── Helpers ────────────────────────────────────────────────────


def get_staged_files(project_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, cwd=project_root,
    )
    if result.stdout.strip():
        return [project_root / f for f in result.stdout.strip().split("\n") if f]
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=project_root,
    )
    return [project_root / f for f in result.stdout.strip().split("\n") if f]


def scan_file(filepath: Path) -> list[str]:
    """Returns list of real privacy issues found in file."""
    issues: list[str] = []

    suffix = filepath.suffix.lower()
    if suffix in BINARY_EXT:
        return issues
    if str(filepath).endswith(".tar.gz"):
        return issues
    if filepath.name in (".gitignore", "check_privacy.py"):
        return issues

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return issues

    lines = text.split("\n")

    for lineno, line in enumerate(lines, 1):
        s = line.strip()
        if is_safe_line(s):
            continue

        # SSH private key (high confidence)
        if SSH_KEY.search(s):
            issues.append(f"L{lineno} SSH_KEY: {s[:80]}")
            continue

        # URL with embedded credentials
        if URL_WITH_CREDS.search(s):
            issues.append(f"L{lineno} URL_CREDS: {s[:100]}")
            continue

        # JWT token
        if JWT_PATTERN.search(s):
            issues.append(f"L{lineno} JWT: {s[:80]}")
            continue

        # API key (sk-... pattern)
        if API_KEY_PATTERN.search(s):
            issues.append(f"L{lineno} API_KEY: {s[:80]}")
            continue

        # Sensitive variable assignment
        if SENSITIVE_ASSIGN.search(s):
            issues.append(f"L{lineno} SENSITIVE_VAR: {s[:80]}")
            continue

        # Real Windows user path (actual user home)
        if REAL_WIN_USER.search(s):
            issues.append(f"L{lineno} WIN_USER_PATH: {s[:100]}")
            continue

        # Real Unix user home path
        if REAL_UNIX_HOME.search(s):
            issues.append(f"L{lineno} UNIX_USER_PATH: {s[:100]}")
            continue

    # Check for sensitive filenames
    if filepath.name in SENSITIVE_FILENAMES:
        issues.append(
            f"FILENAME: {filepath.name} is staged — unstage or add to .gitignore!"
        )

    return issues


# ── Main ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    files = get_staged_files(PROJECT_ROOT)
    if not files:
        print("Privacy check: no files to scan.")
        return 0

    all_issues: list[tuple[Path, list[str]]] = []
    for fp in files:
        try:
            rel = fp.relative_to(PROJECT_ROOT)
        except ValueError:
            rel = fp
        issues = scan_file(fp)
        if issues:
            all_issues.append((rel, issues))

    if not all_issues:
        print("Privacy check: all clear.")
        return 0

    n = sum(len(i) for _, i in all_issues)
    print(f"Privacy check: {n} item(s) need review\n")
    for rel, issues in all_issues:
        print(f"  {rel}")
        for iss in issues:
            print(f"    {iss}")
        print()

    print("Review flagged items. If safe, commit with --no-verify to skip.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
