# Hermes Token Dashboard

A multi-interface dashboard for viewing token consumption captured by the local proxy. Shows per-model token usage, cache hit rates, request counts with time-range filtering and auto-refresh.

## Tech Stack
- Python 3.11+ with FastAPI backend
- Vue 3 CDN + Chart.js for Web frontend
- Textual 8.x for TUI mode
- pywebview (Edge WebView2) for native desktop wrapper
- tkinter + ttk for standalone native GUI

## Interfaces (4 modes)

| Flag | Mode | Entry |
|------|------|-------|
| (default) | Desktop | pywebview wraps web frontend in native window |
| `--web` | Web Server | FastAPI + Vue 3, opens browser at :8765 |
| `--tui` | Terminal TUI | Textual app with keyboard bindings |
| N/A | Native GUI | Standalone tkinter app (gui.py) |

## Project Structure
```
hermes_token_dash/
  __init__.py
  app.py            # Textual TUI App
  parser_claude.py  # Legacy JSONL parser (not used at runtime)
  parser_hermes.py  # Legacy Hermes Agent SQLite parser (not used at runtime)
  models.py         # Data models + pricing
  widgets.py        # Custom Textual widgets (PulseDot, ModelsBox, SummaryBox)
  server.py         # FastAPI REST API server
  desktop.py        # pywebview desktop wrapper
  gui.py            # tkinter native GUI
  config.py         # Configuration (paths, defaults)
  static/
    index.html      # Vue 3 frontend (hero cards, Chart.js, tabs)
main.py             # Entry point with mode dispatch
```

## Key Decisions
- Web is the primary interface (Vue 3 CDN, no build step)
- All interfaces read the same proxy database backend
- Proxy database is the only runtime data source; do not scan agent history logs
- Chart.js for trends, real CSS <div> progress bars (not ASCII)
- Dark theme with accent color scheme
- 5s auto-refresh across all interfaces

## Data Sources (Windows)
- Local proxy DB: `~/.token-dashboard/token-dashboard.db`
- Claude/Codex/Hermes local history logs are not runtime data sources
- Virtual env: `.venv/Scripts/activate`

## REST API Endpoints
| Endpoint | Description |
|----------|-------------|
| GET `/api/models` | List models with request counts |
| GET `/api/stats` | Per-model per-date stats (time/model filter) |
| GET `/api/summary` | Aggregated totals |
| GET `/api/logs` | Paginated raw request logs |
| GET `/api/trends` | Daily-aggregated data for charts |
| POST `/api/refresh` | Force data reload from disk |
