"""测试配置：共用 fixtures 与 mock 设置。

Mock 所有 parser 层函数，避免测试读磁盘。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# 项目根加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes_token_dash.models import ModelPricing, ModelStats, TokenUsage

# ── Sample 数据 ────────────────────────────────────────────────────

SAMPLE_PRICING = {
    "test-model-a": ModelPricing(1.0, 2.0, 0.1, 0.2),
    "test-model-b": ModelPricing(3.0, 6.0, 0.3, 0.6),
    "deepseek-v4-pro": ModelPricing(0.55, 0.19),
}


def _make_usage(
    request_id: str,
    model: str,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_read: int = 0,
    cache_creation: int = 0,
    timestamp: datetime | None = None,
    data_source: str = "claude",
    status_code: int = 200,
    latency_ms: float = 150.0,
    first_token_ms: float = 50.0,
) -> TokenUsage:
    """快速构造一个 TokenUsage 记录。"""
    if timestamp is None:
        timestamp = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
    return TokenUsage(
        request_id=request_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_creation=cache_creation,
        timestamp=timestamp,
        data_source=data_source,
        status_code=status_code,
        latency_ms=latency_ms,
        first_token_ms=first_token_ms,
    )


def _make_stats(
    model: str,
    date: str,
    total_input: int = 1000,
    total_output: int = 500,
    cache_read: int = 0,
    cache_create: int = 0,
    requests: int = 1,
    requests_cache: int = 0,
) -> ModelStats:
    """快速构造 ModelStats。"""
    s = ModelStats(
        model=model,
        date=date,
        total_input=total_input,
        total_output=total_output,
        total_cache_read=cache_read,
        total_cache_creation=cache_create,
        request_count=requests,
        requests_with_cache=requests_cache,
    )
    s.compute_derived()
    return s


# 多条测试用 TokenUsage，覆盖多模型、多日期、多状态码
_now = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
_yesterday = _now - timedelta(days=1)
_week_ago = _now - timedelta(days=8)
_month_ago = _now - timedelta(days=35)

SAMPLE_USAGES: list[TokenUsage] = [
    _make_usage("r1", "test-model-a", 2000, 800, 64, 0, _now),
    _make_usage("r2", "test-model-a", 1500, 600, 0, 128, _now),
    _make_usage("r3", "test-model-b", 3000, 1200, 0, 0, _yesterday),
    _make_usage("r4", "test-model-b", 1000, 400, 256, 0, _yesterday, status_code=500),
    _make_usage("r5", "test-model-a", 500, 200, 0, 0, _week_ago),
    _make_usage("r6", "test-model-a", 800, 300, 0, 0, _week_ago, data_source="hermes"),
    _make_usage("r7", "test-model-b", 4000, 2000, 0, 64, _month_ago, latency_ms=300.0),
    _make_usage("r8", "deepseek-v4-pro", 10000, 5000, 500, 100, _now, latency_ms=0),
]

SAMPLE_MODELS: list[str] = ["test-model-a", "test-model-b", "deepseek-v4-pro"]

SAMPLE_STATS: list[ModelStats] = [
    _make_stats("test-model-a", "2026-06-25", 3500, 1400, 64, 128, 2, 1),
    _make_stats("test-model-b", "2026-06-25", 3000, 1200, 256, 0, 1, 0),
    _make_stats("test-model-a", "2026-06-24", 1500, 600, 0, 0, 1, 0),
    _make_stats("test-model-b", "2026-06-17", 1000, 400, 0, 0, 1, 0),
    _make_stats("test-model-a", "2026-05-21", 800, 300, 0, 0, 1, 0),
]


# ── Mock helpers ────────────────────────────────────────────────────

def _mock_parse_jsonl(_file: Path) -> list[TokenUsage]:
    """模拟 parse_jsonl：返回 SAMPLE_USAGES。"""
    return list(SAMPLE_USAGES)


def _mock_scan_claude_jsonls() -> list[Path]:
    """模拟 scan_claude_jsonls：返回一个假文件路径。"""
    return [Path("/fake/session.jsonl")]


def _mock_parse_hermes_sessions() -> list[TokenUsage]:
    """模拟 parse_hermes_sessions：返回空。"""
    return []


def _mock_scan_codex_jsonls() -> list[Path]:
    return []


def _mock_parse_codex_jsonl(_file: Path) -> list[TokenUsage]:
    return []


def _mock_parse_proxy_request_logs() -> list[TokenUsage]:
    return list(SAMPLE_USAGES)


def _mock_aggregate_by_model_date(
    records: list[TokenUsage], time_filter: str = "all", tz_offset: int = 8
) -> list[ModelStats]:
    """模拟 aggregate_by_model_date：返回 SAMPLE_STATS 的子集。"""
    return [s for s in SAMPLE_STATS if _time_matches(s, time_filter)]


def _time_matches(s: ModelStats, time_filter: str) -> bool:
    """简单时间过滤。"""
    if time_filter == "all":
        return True
    # 简化：全部返回
    return True


def _mock_get_available_models(_records: list[TokenUsage]) -> list[str]:
    return list(SAMPLE_MODELS)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _mock_parsers(monkeypatch):
    """自动 mock 所有 parser 层函数。在 server 模块级别替换。"""
    from hermes_token_dash import server as srv

    monkeypatch.setattr(srv, "parse_proxy_request_logs", _mock_parse_proxy_request_logs)
    monkeypatch.setattr(
        srv, "aggregate_by_model_date", _mock_aggregate_by_model_date
    )
    monkeypatch.setattr(srv, "get_available_models", _mock_get_available_models)
    # 替换 MODEL_PRICING
    monkeypatch.setattr(
        srv, "MODEL_PRICING", SAMPLE_PRICING.copy()
    )


@pytest.fixture
def client():
    """FastAPI TestClient。"""
    from hermes_token_dash.server import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def sample_usages() -> list[TokenUsage]:
    return list(SAMPLE_USAGES)
