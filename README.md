# Hermes Token Dashboard

Multi-interface dashboard for AI coding tool token consumption analytics. Visualize per-model token usage, cache hit rates, request counts, and estimated costs across Claude Code and Hermes Agent sessions — with time-range filtering and auto-refresh.

## Interfaces (4 modes)

| Flag | Mode | Description |
|------|------|-------------|
| _(default)_ | Desktop | Native window via pywebview (Edge WebView2) |
| `--web` | Web Server | FastAPI + Vue 3, opens browser at `http://127.0.0.1:8765` |
| `--tui` | Terminal TUI | Textual framework with keyboard shortcuts |

## Features

- **Multi-source data**: Claude Code JSONL sessions + Hermes Agent SQLite session DB
- **4 models tracked**: DeepSeek V4 Pro, DeepSeek V4 Flash, MiMo V2.5, MiMo V2.5 Pro
- **Real-time metrics**: input/output tokens, cache hit rate, estimated cost (¥)
- **Time filters**: All Time, Today, Last 7 Days, Last 30 Days
- **Trend charts**: daily aggregated token usage via Chart.js
- **Paginated logs**: full request history with model, tokens, latency, cost
- **Per-provider stats**: provider-level aggregation with success rate and latency
- **Dynamic pricing**: runtime pricing overrides via REST API
- **5-second auto-refresh** across all interfaces

## Screenshot

```
┌─────────────────────────────────────────────────────────┐
│  Hermes Token Dashboard                          🔄 5s  │
├─────────────────────────────────────────────────────────┤
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ 总请求   │ │ token输入 │ │ token输出 │ │ 估算费用   │ │
│  │  2,644   │ │  498M    │ │  52.3M   │ │  ¥8,427.50 │ │
│  └──────────┘ └──────────┘ └──────────┘ └────────────┘ │
│                                                         │
│  Model                   Requests     Input    Hit Rate│
│  ───────────────────────────────────────────────────── │
│  DeepSeek V4 Pro  █████  1,892      380M      78.2%   │
│  DeepSeek V4 Flash ███     452       72M      62.1%   │
│  MiMo V2.5         ██      218       35M      45.3%   │
│  MiMo V2.5 Pro     █        82       11M      38.7%   │
│                                                         │
│  📈 Daily Token Usage (30d)                             │
│  ┌─────────────────────────────────────────────────┐   │
│  │  ██                                                  │
│  │  ██  ██                                              │
│  │  ██  ██  ██  ██                                      │
│  │  ██  ██  ██  ██  ██  ██                              │
│  │  ██  ██  ██  ██  ██  ██  ██  ██                      │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Python 3.11+
- Windows 10+ (primary platform; macOS/Linux for Web mode)

### Quick Start

```bash
git clone git@github.com:ForStudyA/Token-Dashbroad.git
cd Token-Dashbroad

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux

# Install
pip install -e .

# Run web mode (no native deps needed)
python main.py --web
```

### Install with optional dependencies

```bash
# Web only (minimal)
pip install -e .

# Desktop mode (requires Edge WebView2 — preinstalled on Windows 10+)
pip install -e ".[desktop]"

# TUI mode
pip install -e ".[tui]"

# Everything
pip install -e ".[all]"
```

### Development install

```bash
git clone git@github.com:ForStudyA/Token-Dashbroad.git
cd Token-Dashbroad
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[all]"
pip install pytest
```

## Usage

```bash
# Desktop app (default)
python main.py

# Web server — opens browser at http://127.0.0.1:8765
python main.py --web

# Terminal TUI
python main.py --tui
```

### Web mode is the primary interface

Start the server and open `http://127.0.0.1:8765` in any browser. All data, charts, and controls are client-side rendered — no page reloads.

## Data Sources

The dashboard reads from two sources automatically:

| Source | Location | Format |
|--------|----------|--------|
| Claude Code | `~/.claude/projects/*/<session>.jsonl` | JSONL — one JSON object per line |
| Hermes Agent | `~/AppData/Local/hermes/state.db` + `profiles/*/state.db` | SQLite — session message store |

No configuration needed — paths are resolved from your home directory.

## REST API

Base URL: `http://127.0.0.1:8765`

All endpoints accept optional query parameters for filtering. Time values: `all` (default), `today`, `7d`, `30d`.

### GET `/api/summary`

Aggregated totals across all records.

```bash
curl http://127.0.0.1:8765/api/summary
```

Response:
```json
{
  "input": 498000000,
  "output": 52300000,
  "cost": 8427.50,
  "requests": 2644,
  "hit_rate": 72.3,
  "groups": 156
}
```

Parameters: `time`, `model`, `source`

### GET `/api/models`

List available models with request counts.

```bash
curl http://127.0.0.1:8765/api/models
```

Response:
```json
{
  "models": [
    {"name": "deepseek-v4-pro", "count": 1892},
    {"name": "deepseek-v4-flash", "count": 452}
  ],
  "total": 2644
}
```

Parameters: `source`

### GET `/api/stats`

Per-model per-date statistics.

```bash
curl "http://127.0.0.1:8765/api/stats?time=7d&model=deepseek-v4-pro"
```

Response:
```json
[
  {
    "model": "deepseek-v4-pro",
    "date": "2026-06-25",
    "input": 8500000,
    "output": 920000,
    "cache_read": 3200000,
    "cache_create": 450000,
    "requests": 87,
    "requests_cache": 68,
    "hit_rate": 78.2,
    "cost": 4.85
  }
]
```

Parameters: `time`, `model`, `source`

### GET `/api/trends`

Daily aggregated data for charts.

```bash
curl "http://127.0.0.1:8765/api/trends?time=30d"
```

Response:
```json
[
  {
    "date": "2026-06-01",
    "requests": 45,
    "input": 5200000,
    "output": 480000,
    "cache_read": 1800000,
    "cache_creation": 220000,
    "cost": 3.12
  }
]
```

Parameters: `time` (default `30d`), `source`, `model`

### GET `/api/logs`

Paginated raw request logs.

```bash
curl "http://127.0.0.1:8765/api/logs?page=1&limit=10&time=7d"
```

Response:
```json
{
  "items": [
    {
      "request_id": "req_abc123",
      "model": "deepseek-v4-pro",
      "input_tokens": 28456,
      "output_tokens": 1234,
      "cache_read": 8000,
      "cache_creation": 1500,
      "timestamp": "2026-06-25T14:22:00+00:00",
      "cost": 0.0158,
      "data_source": "claude_code",
      "status_code": 200,
      "latency_ms": 2340.5,
      "first_token_ms": 450.2
    }
  ],
  "total": 2644,
  "page": 1,
  "limit": 10
}
```

Parameters: `time`, `model`, `source`, `page` (default 1), `limit` (default 50, max 500)

### GET `/api/providers`

Per-provider aggregated stats with success rate and latency.

```bash
curl "http://127.0.0.1:8765/api/providers?time=30d"
```

Response:
```json
[
  {
    "provider": "deepseek",
    "request_count": 1892,
    "total_input_tokens": 380000000,
    "total_output_tokens": 42000000,
    "total_cache_read": 150000000,
    "total_cache_creation": 22000000,
    "total_cost": 6200.50,
    "success_rate": 99.2,
    "avg_latency_ms": 2340.5,
    "models": ["deepseek-v4-flash", "deepseek-v4-pro"]
  }
]
```

Parameters: `time`, `model`, `source`

### GET `/api/pricing`

Current model pricing configuration.

```bash
curl http://127.0.0.1:8765/api/pricing
```

### PUT `/api/pricing`

Update pricing at runtime (in-memory only).

```bash
curl -X PUT http://127.0.0.1:8765/api/pricing \
  -H "Content-Type: application/json" \
  -d '[{"model": "my-model", "input_price": 1.0, "output_price": 5.0}]'
```

### POST `/api/refresh`

Force reload data from disk.

```bash
curl -X POST http://127.0.0.1:8765/api/refresh
```

## Example: Python Client

```python
import requests

BASE = "http://127.0.0.1:8765"

# Get summary stats
summary = requests.get(f"{BASE}/api/summary", params={"time": "7d"}).json()
print(f"Requests: {summary['requests']}, Cost: ¥{summary['cost']}")

# List models
models = requests.get(f"{BASE}/api/models").json()
for m in models["models"]:
    print(f"  {m['name']}: {m['count']} requests")

# Get daily trends for charting
trends = requests.get(f"{BASE}/api/trends", params={"time": "30d"}).json()
dates = [t["date"] for t in trends]
costs = [t["cost"] for t in trends]

# Paginated logs
page1 = requests.get(f"{BASE}/api/logs", params={"page": 1, "limit": 20}).json()
for item in page1["items"]:
    print(f"[{item['timestamp'][:10]}] {item['model']}: "
          f"{item['input_tokens']}→{item['output_tokens']} tokens, "
          f"¥{item['cost']:.4f}")

# Refresh data
requests.post(f"{BASE}/api/refresh")
```

## Example: JavaScript (Browser)

```javascript
// Fetch summary
const summary = await fetch('/api/summary?time=7d').then(r => r.json());
console.log(`Total cost: ¥${summary.cost}`);

// Fetch trends for Chart.js
const trends = await fetch('/api/trends?time=30d').then(r => r.json());
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: trends.map(t => t.date),
    datasets: [{
      label: 'Input Tokens',
      data: trends.map(t => t.input),
    }]
  }
});

// Fetch logs with pagination
const logs = await fetch('/api/logs?page=1&limit=50').then(r => r.json());
logs.items.forEach(item => {
  console.log(`${item.model}: ${item.input_tokens} tokens, ¥${item.cost}`);
});
```

## Project Structure

```
hermes-token-dash/
├── main.py                          # Entry point with mode dispatch
├── pyproject.toml                   # Project metadata and dependencies
├── hermes_token_dash/
│   ├── __init__.py
│   ├── server.py                    # FastAPI REST API (7 endpoints)
│   ├── desktop.py                   # pywebview desktop wrapper
│   ├── app.py                       # Textual TUI
│   ├── gui.py                       # tkinter native GUI
│   ├── models.py                    # TokenUsage, ModelStats, pricing
│   ├── parser_claude.py             # Claude Code JSONL parser
│   ├── parser_hermes.py             # Hermes Agent SQLite parser
│   ├── config.py                    # Paths, defaults, display names
│   ├── widgets.py                   # Custom Textual widgets
│   └── static/
│       └── index.html               # Vue 3 + Chart.js frontend
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, uvicorn |
| Web Frontend | Vue 3 CDN, Chart.js, no build step |
| Desktop | pywebview (Edge WebView2) |
| TUI | Textual 8.x |
| Data | JSONL (Claude), SQLite (Hermes) |

## Model Pricing

Default pricing per 1M tokens (USD):

| Model | Input | Output |
|-------|-------|--------|
| DeepSeek V4 Pro | $0.55 | $0.19 |
| DeepSeek V4 Flash | $0.09 | $0.36 |
| MiMo V2.5 | $0.50 | $2.00 |
| MiMo V2.5 Pro | $0.50 | $2.00 |
| Claude Sonnet 4 | $3.00 | $15.00 |
| Claude Opus 4 | $15.00 | $75.00 |

Override pricing at runtime via `PUT /api/pricing`.

## Configuration

All paths and defaults are in `hermes_token_dash/config.py`. No config files needed — everything is auto-detected from your home directory.

Key constants:
- `HERMES_MAIN_DB`: `~/AppData/Local/hermes/state.db`
- `CLAUDE_PROJECTS_DIR`: `~/.claude/projects/`
- `DEFAULT_PORT`: 8765
- `AUTO_REFRESH_INTERVAL`: 5 seconds

## License

MIT
