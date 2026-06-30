#!/usr/bin/env python
"""
Privacy check script — run before every git commit to scan for:
  - Hardcoded absolute paths (Windows / macOS / Linux)
  - API keys, tokens, secrets, passwords
  - Sensitive config files accidentally staged

Usage:
    python scripts/check_privacy.py

Returns exit code 0 if clean, 1 if issues found.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# ── Patterns ───────────────────────────────────────────────────

# Windows absolute path: C:\Users\<name>\ or D:\code\...
WINDOWS_ABS_PATH = re.compile(
    r"[a-zA-Z]:\\(?:Users|code|Program|Windows|Temp|tmp)\S",
    re.IGNORECASE,
)

# Unix absolute path (excluding test data and well-known safe paths)
UNIX_ABS_PATH = re.compile(
    r"""
    (?<![@\w/])                                    # not in URL/import/string prefix
    /(?:home|private|etc|var|opt|usr|root)/\S+
    """,
    re.VERBOSE,
)

# Real API keys: quoted strings starting with sk- (OpenAI) or similar
API_KEY_PATTERN = re.compile(
    r"""(['\"])                                        # opening quote
    (?:sk-[a-zA-Z0-9]{10,})                           # OpenAI-style sk-...
    \1                                                # closing same quote
    """,
    re.VERBOSE,
)

# Sensitive variable assignment: var name itself contains key/secret/token/password
SENSITIVE_VAR_ASSIGN = re.compile(
    r"""^\s*
    (
        (?:api[_-]?key|apikey|secret|secret_key|access_key)
        \s*[=:]\s*['\"].+?['\"]
        |
        \w*(?:password|passwd|pwd)\w*
        \s*[=:]\s*['\"].+?['\"]
        |
        \w*token\w*
        \s*[=:]\s*['\"]\w{16,}['\"]
    )
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)

SENSITIVE_FILES = frozenset({
    "mimo_cookies.json",
    "deepseek_key.txt",
})

# ── Whitelist patterns — known safe / intentional ──────────────

WHITELIST_PATTERNS = [
    re.compile(r"https?://"),                      # URLs
    re.compile(r"git@github\.com:"),               # git remote
    re.compile(r"\.venv/"),                        # venv path
    re.compile(r"/usr/bin/bash"),                  # known pricing string
    re.compile(r"/usr/share/"),                    # system share paths
]

TEST_FILE_PATTERNS = [
    re.compile(r"/home/user"),
    re.compile(r"/tmp/"),
]


def is_whitelisted(line: str) -> bool:
    return any(p.search(line) for p in WHITELIST_PATTERNS)


def is_test_fake_path(line: str) -> bool:
    """Check if this line is a test file with obviously fake paths."""
    return any(p.search(line) for p in TEST_FILE_PATTERNS)


# ── Helpers ────────────────────────────────────────────────────

def get_staged_files(project_root: Path) -> list[Path]:
    """Return list of staged files (or all tracked files if nothing staged)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, cwd=project_root,
    )
    if result.stdout.strip():
        return [project_root / f for f in result.stdout.strip().split("\n") if f]

    # Fallback: check all tracked files
    result = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, cwd=project_root,
    )
    return [project_root / f for f in result.stdout.strip().split("\n") if f]


def is_test_file(filepath: Path) -> bool:
    """Check if file is under a tests/ directory."""
    return "tests" in filepath.parts or filepath.parent.name == "tests"


def check_file(filepath: Path) -> list[str]:
    """Scan a single file for privacy issues. Returns list of issues."""
    issues: list[str] = []

    # Skip binary / non-text files
    suffix = filepath.suffix.lower()
    if suffix in {".pyc", ".exe", ".dll", ".so", ".dylib", ".png", ".jpg",
                  ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".eot",
                  ".ttf", ".pdf", ".zip", ".tar", ".gz", ".7z", ".lock"}:
        return issues

    # Skip .gitignore and this script
    if filepath.name in (".gitignore", "check_privacy.py"):
        return issues

    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return issues

    is_test = is_test_file(filepath)
    lines = text.split("\n")

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        if is_whitelisted(stripped):
            continue

        # Check Windows absolute paths
        if WINDOWS_ABS_PATH.search(stripped):
            if is_test and is_test_fake_path(stripped):
                continue
            issues.append(
                f"  L{lineno:>4} | [ABSOLUTE_PATH] {stripped[:100]}"
            )

        # Check Unix absolute paths
        for m in UNIX_ABS_PATH.finditer(stripped):
            path = m.group()
            # Skip well-known safe system paths
            if any(k in path for k in ("/usr/bin", "/usr/share", "/usr/lib",
                                        "/etc/", "/var/")):
                continue
            # In test files, /home/user/ and /tmp/ are obviously fake
            if is_test and is_test_fake_path(stripped):
                continue
            issues.append(
                f"  L{lineno:>4} | [ABSOLUTE_PATH] {stripped[:100]}"
            )

        # Check API key patterns (only real sk- keys, not paths)
        if API_KEY_PATTERN.search(stripped):
            issues.append(
                f"  L{lineno:>4} | [API_KEY] {stripped[:80]}"
            )

        # Check sensitive variable assignments
        if SENSITIVE_VAR_ASSIGN.search(stripped):
            if is_test and is_test_fake_path(stripped):
                continue
            issues.append(
                f"  L{lineno:>4} | [SENSITIVE_VAR] {stripped[:80]}"
            )

    # Check filename for sensitive files
    if filepath.name in SENSITIVE_FILES:
        issues.append(
            f"  [SENSITIVE_FILE] {filepath.name} is staged — "
            f"add to .gitignore and unstage!"
        )

    return issues


# ── Main ────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    print(f"Privacy check -- {PROJECT_ROOT.name}")
    print(f"   Scanning: {PROJECT_ROOT}")
    print()

    files = get_staged_files(PROJECT_ROOT)
    if not files:
        print("   No files to check.")
        return 0

    all_issues: list[tuple[Path, list[str]]] = []
    for fp in files:
        try:
            rel = fp.relative_to(PROJECT_ROOT)
        except ValueError:
            rel = fp
        issues = check_file(fp)
        if issues:
            all_issues.append((rel, issues))

    if not all_issues:
        print("All clear -- no privacy issues found.")
        return 0

    print("Privacy issues detected:\n")
    for rel, issues in all_issues:
        print(f"  File: {rel}")
        for issue in issues:
            print(f"     {issue}")
        print()

    print("Please fix these before committing.")
    print("Run `python scripts/check_privacy.py` again after fixing.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
