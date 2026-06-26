# Hermes Token Dashboard / Hermes Token 仪表盘

[English](#english) | [中文](#中文)

---

## English

Multi-interface dashboard for AI coding tool token consumption analytics. Visualize per-model token usage, cache hit rates, request counts, and estimated costs across Claude Code, Codex CLI, and Hermes Agent sessions — with auto-detected filtering and auto-refresh.

### Interfaces

| Flag | Mode | Description |
|------|------|-------------|
| _(default)_ | Desktop | Native window via pywebview (Edge WebView2) |
| `--web` | Web Server | FastAPI + Vue 3, opens browser at `http://127.0.0.1:8765` |
| `--tui` | Terminal TUI | Textual framework with keyboard shortcuts |

### Features

- **Multi-source data**: Claude Code JSONL + Codex CLI JSONL + Hermes Agent SQLite session DB
- **Auto-detected filters**: data sources and agents are discovered from actual records, not hardcoded
- **Agent filter**: distinguish between `cli`, `sdk-cli`, `claude-vscode` (Claude Code) and `Codex Desktop`, `codex_exec` (Codex)
- **Profile filter**: filter by Hermes profile (default, named profiles)
- **Real-time metrics**: input/output tokens, cache hit rate, estimated cost (¥)
- **Time filters**: All Time, Today, Last 7 Days, Last 30 Days
- **Trend charts**: daily aggregated token usage via Chart.js
- **Paginated logs**: full request history with model, tokens, latency, cost
- **Per-provider stats**: provider-level aggregation with success rate and latency
- **Dynamic pricing**: runtime pricing overrides via REST API
- **5-second auto-refresh** across all interfaces

### Known Issues

| Issue | Details |
|-------|---------|
| **Hermes multi-turn sessions** | Hermes stores token usage at session level only (`sessions` table). `messages.token_count` is `NULL` in the current Hermes version. A multi-turn conversation appears as a single log entry with cumulative totals — cannot be split into per-turn records like Claude Code or Codex. |
| **Chatbox not supported** | Chatbox stores data in Chrome's IndexedDB (LevelDB with `idb_cmp1` comparator, Snappy compression, CBOR encoding). Cannot be parsed while the app is running; needs app-level export support. |
| **Timestamp precision** | Dashboard displays timestamps at second precision. Adjacent sessions may appear to share the same timestamp. |

### Installation

**Prerequisites**: Python 3.11+, Windows 10+ (primary platform; macOS/Linux for Web mode)

```bash
git clone git@github.com:ForStudyA/Token-Dashboard.git
cd Token-Dashboard

python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -e .

# Run web mode
python main.py --web
```

Optional dependencies:
```bash
pip install -e ".[desktop]"   # Desktop mode (needs Edge WebView2)
pip install -e ".[tui]"       # TUI mode
pip install -e ".[all]"       # Everything
```

### Data Sources

| Source | Location | Format | Granularity |
|--------|----------|--------|-------------|
| Claude Code | `~/.claude/projects/*/*.jsonl` | JSONL | Per API call |
| Codex CLI | `~/.codex/sessions/*/rollout-*.jsonl` | JSONL | Per turn |
| Hermes Agent | `~/AppData/Local/hermes/state.db` | SQLite | Per session |

No configuration needed — paths are auto-detected.

### REST API

Base URL: `http://127.0.0.1:8765`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/summary` | Aggregated totals |
| GET | `/api/models` | Available models with counts |
| GET | `/api/stats` | Per-model per-date statistics |
| GET | `/api/trends` | Daily aggregated chart data |
| GET | `/api/logs` | Paginated raw request logs |
| GET | `/api/providers` | Per-provider aggregated stats |
| GET | `/api/sources` | Auto-detected data sources |
| GET | `/api/agents` | Available agents with counts |
| GET | `/api/profiles` | Available Hermes profiles |
| GET | `/api/pricing` | Current pricing config |
| PUT | `/api/pricing` | Update pricing at runtime |
| POST | `/api/refresh` | Force reload data |

Common parameters: `time` (all/today/7d/30d), `model`, `source`, `profile`, `agent`

### Project Structure

```
hermes-token-dash/
├── main.py
├── pyproject.toml
├── hermes_token_dash/
│   ├── server.py                    # FastAPI REST API
│   ├── desktop.py                   # pywebview desktop
│   ├── app.py                       # Textual TUI
│   ├── models.py                    # TokenUsage, ModelStats, pricing
│   ├── parser_claude.py             # Claude Code JSONL parser
│   ├── parser_codex.py              # Codex CLI JSONL parser
│   ├── parser_hermes.py             # Hermes Agent SQLite parser
│   ├── config.py                    # Paths and defaults
│   └── static/
│       └── index.html               # Vue 3 + Chart.js frontend
└── tests/
```

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, uvicorn |
| Web Frontend | Vue 3 CDN, Chart.js, no build step |
| Desktop | pywebview (Edge WebView2) |
| TUI | Textual |
| Data | JSONL (Claude/Codex), SQLite (Hermes) |

### License

MIT

---

## 中文

AI 编程工具 token 消耗分析仪表盘，多界面支持。可按时段、模型、数据源、Agent、Profile 筛选，展示 Claude Code、Codex CLI 和 Hermes Agent 的 token 用量、缓存命中率、请求次数和预估费用。

### 启动方式

| 参数 | 模式 | 说明 |
|------|------|------|
| _(默认)_ | 桌面应用 | 原生窗口（pywebview + Edge WebView2） |
| `--web` | Web 服务 | FastAPI + Vue 3，浏览器打开 `http://127.0.0.1:8765` |
| `--tui` | 终端 TUI | Textual 框架，键盘快捷键操作 |

### 功能

- **多数据源**：Claude Code JSONL + Codex CLI JSONL + Hermes Agent SQLite
- **自动发现筛选**：数据源和 Agent 列表从实际数据中自动提取，无需手动维护
- **Agent 筛选**：区分 Claude Code 的 `cli`/`sdk-cli`/`claude-vscode` 和 Codex 的 `Codex Desktop`/`codex_exec`
- **Profile 筛选**：按 Hermes 的 profile 过滤（default、命名 profile）
- **实时指标**：输入/输出 token、缓存命中率、预估费用（¥）
- **时间筛选**：全部、今天、最近 7 天、最近 30 天
- **趋势图表**：Chart.js 日聚合 token 消耗图
- **分页日志**：完整请求历史，包含模型、token、延迟、费用
- **Provider 统计**：按服务商聚合，含成功率和延迟
- **动态定价**：通过 REST API 运行时覆盖定价
- **5 秒自动刷新**

### 已知问题

| 问题 | 详情 |
|------|------|
| **Hermes 多轮对话** | Hermes 只记录会话级别的 token 总量（`sessions` 表）。当前版本中 `messages.token_count` 为 `NULL`。一次多轮对话在日志中显示为一条记录，无法像 Claude Code 或 Codex 那样按每一轮拆分。 |
| **Chatbox 不支持** | Chatbox 数据存储在 Chrome IndexedDB 中（LevelDB，使用 `idb_cmp1` 比较器、Snappy 压缩、CBOR 编码）。应用运行时无法解析，需要应用层面导出数据。 |
| **时间戳精度** | 仪表盘显示时间精确到秒。相邻会话可能显示相同时间。 |

### 安装

**环境要求**：Python 3.11+，Windows 10+（主要平台；macOS/Linux 支持 Web 模式）

```bash
git clone git@github.com:ForStudyA/Token-Dashboard.git
cd Token-Dashboard

python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux

pip install -e .

# 启动 Web 模式
python main.py --web
```

可选依赖：
```bash
pip install -e ".[desktop]"   # 桌面模式（需要 Edge WebView2）
pip install -e ".[tui]"       # TUI 模式
pip install -e ".[all]"       # 全部
```

### 数据来源

| 来源 | 位置 | 格式 | 粒度 |
|------|------|------|------|
| Claude Code | `~/.claude/projects/*/*.jsonl` | JSONL | 每次 API 调用 |
| Codex CLI | `~/.codex/sessions/*/rollout-*.jsonl` | JSONL | 每轮对话 |
| Hermes Agent | `~/AppData/Local/hermes/state.db` | SQLite | 每个会话 |

无需配置，路径自动检测。

### REST API

根路径：`http://127.0.0.1:8765`

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/summary` | 聚合总计 |
| GET | `/api/models` | 可用模型及请求数 |
| GET | `/api/stats` | 按模型和日期统计 |
| GET | `/api/trends` | 日聚合图表数据 |
| GET | `/api/logs` | 分页请求日志 |
| GET | `/api/providers` | 按服务商聚合统计 |
| GET | `/api/sources` | 自动发现的数据源列表 |
| GET | `/api/agents` | 可用 Agent 及请求数 |
| GET | `/api/profiles` | 可用 Hermes profile 列表 |
| GET | `/api/pricing` | 当前定价配置 |
| PUT | `/api/pricing` | 运行时更新定价 |
| POST | `/api/refresh` | 强制重新加载数据 |

通用参数：`time`（all/today/7d/30d）、`model`、`source`、`profile`、`agent`

### 项目结构

```
hermes-token-dash/
├── main.py
├── pyproject.toml
├── hermes_token_dash/
│   ├── server.py                    # FastAPI REST API
│   ├── desktop.py                   # pywebview 桌面版
│   ├── app.py                       # Textual TUI
│   ├── models.py                    # 数据模型和定价
│   ├── parser_claude.py             # Claude Code JSONL 解析
│   ├── parser_codex.py              # Codex CLI JSONL 解析
│   ├── parser_hermes.py             # Hermes Agent SQLite 解析
│   ├── config.py                    # 路径和默认值
│   └── static/
│       └── index.html               # Vue 3 + Chart.js 前端
└── tests/
```

### 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.11+, FastAPI, uvicorn |
| Web 前端 | Vue 3 CDN, Chart.js，无构建步骤 |
| 桌面 | pywebview (Edge WebView2) |
| TUI | Textual |
| 数据 | JSONL（Claude/Codex），SQLite（Hermes） |

### 许可证

MIT
