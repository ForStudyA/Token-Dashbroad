"""Hermes Token Dashboard — Native Desktop App.

Wraps the Vue 3 frontend in a native OS window via pywebview (Edge WebView2).
No browser needed — looks and behaves like a real desktop application.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.request

import uvicorn
import webview

from hermes_token_dash.server import app as fastapi_app


def main():
    # Start FastAPI in background thread
    def run_server():
        uvicorn.run(fastapi_app, host="127.0.0.1", port=8765,
                    log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Wait for server to be ready (max 10s)
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:8765/api/summary", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)

    # Open native desktop window
    window = webview.create_window(
        title="Hermes Token Dashboard",
        url="http://127.0.0.1:8765",
        width=1400,
        height=850,
        min_size=(1000, 550),
        resizable=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
