"""Hermes Token Dashboard launcher.

Usage:
    python -m hermes_token_dash.main              # Desktop app (default, native window)
    python -m hermes_token_dash.main --web        # Web server (opens browser)
    python -m hermes_token_dash.main --tui        # Terminal TUI
"""

import sys


def main():
    if "--web" in sys.argv:
        from hermes_token_dash.server import main as run_web
        run_web()
    elif "--tui" in sys.argv:
        from hermes_token_dash.app import main as run_tui
        run_tui()
    else:
        from hermes_token_dash.desktop import main as run_desktop
        run_desktop()


if __name__ == "__main__":
    main()
