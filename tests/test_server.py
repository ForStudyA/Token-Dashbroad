"""server.py 单元测试。

覆盖所有 9 个 API 端点、工具函数、边界条件与错误处理。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from hermes_token_dash.models import ModelPricing, TokenUsage


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /
# ═══════════════════════════════════════════════════════════════════

class TestIndexRoute:
    """测试根路径与静态文件。"""

    def test_index_returns_html(self, client):
        """GET / 返回 index.html (FileResponse)。"""
        resp = client.get("/")
        assert resp.status_code == 200
        # FileResponse 的内容类型
        assert "text/html" in resp.headers.get("content-type", "")


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/models
# ═══════════════════════════════════════════════════════════════════

class TestModelsEndpoint:
    """测试 /api/models 端点。"""

    def test_returns_model_list(self, client):
        """正常返回模型列表与总数。"""
        resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "total" in data
        assert data["total"] == 8  # SAMPLE_USAGES 共 8 条
        models = {m["name"]: m["count"] for m in data["models"]}
        assert "test-model-a" in models

    def test_filter_by_source_claude(self, client):
        """按 source=claude 过滤。"""
        resp = client.get("/api/models?source=claude")
        data = resp.json()
        claude_count = 7  # SAMPLE_USAGES: 7 claude + 1 hermes
        assert data["total"] == claude_count

    def test_filter_by_source_hermes(self, client):
        """按 source=hermes 过滤。"""
        resp = client.get("/api/models?source=hermes")
        data = resp.json()
        hermes_count = 1  # SAMPLE_USAGES: r6 is hermes
        assert data["total"] == hermes_count

    def test_filter_by_nonexistent_source(self, client):
        """不存在的 source 返回空。"""
        resp = client.get("/api/models?source=nonexistent")
        data = resp.json()
        assert data["total"] == 0
        # 模型列表仍返回但 count 为 0
        for m in data["models"]:
            assert m["count"] == 0


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/stats
# ═══════════════════════════════════════════════════════════════════

class TestStatsEndpoint:
    """测试 /api/stats 端点。"""

    def test_returns_stats(self, client):
        """正常返回 stats 列表。"""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_stats_keys(self, client):
        """验证每个 stat 对象的字段名。"""
        resp = client.get("/api/stats")
        data = resp.json()
        expected_keys = {
            "model", "date", "input", "output", "cache_read",
            "cache_create", "requests", "requests_cache", "hit_rate", "cost",
        }
        for s in data:
            assert expected_keys.issubset(s.keys())

    def test_filter_by_model(self, client):
        """按 model 过滤。"""
        resp = client.get("/api/stats?model=test-model-a")
        data = resp.json()
        for s in data:
            assert s["model"] == "test-model-a"

    def test_filter_by_source(self, client):
        """按 source 过滤时应穿透 model 统计。"""
        resp = client.get("/api/stats?source=claude")
        assert resp.status_code == 200

    def test_filter_by_model_no_match(self, client):
        """不存在的 model 返回空。"""
        resp = client.get("/api/stats?model=nonexistent")
        data = resp.json()
        assert data == []

    def test_time_filter(self, client):
        """time 参数传递。"""
        resp = client.get("/api/stats?time=today")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/summary
# ═══════════════════════════════════════════════════════════════════

class TestSummaryEndpoint:
    """测试 /api/summary 端点。"""

    def test_returns_summary(self, client):
        """正常返回汇总数据。"""
        resp = client.get("/api/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "input" in data
        assert "output" in data
        assert "cost" in data
        assert "requests" in data
        assert "hit_rate" in data
        assert "groups" in data

    def test_filter_by_model(self, client):
        """按 model 过滤。"""
        resp = client.get("/api/summary?model=test-model-a")
        assert resp.status_code == 200

    def test_filter_by_source(self, client):
        """按 source 过滤。"""
        resp = client.get("/api/summary?source=hermes")
        assert resp.status_code == 200

    def test_time_filter(self, client):
        """time 参数。"""
        resp = client.get("/api/summary?time=7d")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/logs
# ═══════════════════════════════════════════════════════════════════

class TestLogsEndpoint:
    """测试 /api/logs 端点。"""

    def test_returns_paginated_logs(self, client):
        """正常返回分页日志。"""
        resp = client.get("/api/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "limit" in data
        assert data["page"] == 1
        assert data["limit"] == 50

    def test_logs_keys(self, client):
        """验证 log 条目字段名。"""
        resp = client.get("/api/logs?limit=1")
        items = resp.json()["items"]
        assert len(items) == 1
        r = items[0]
        expected = {
            "request_id", "model", "input_tokens", "output_tokens",
            "cache_read", "cache_creation", "timestamp", "cost",
            "data_source", "status_code", "latency_ms", "first_token_ms",
        }
        assert expected.issubset(r.keys())

    def test_time_filter_today(self, client):
        """time=today 过滤。"""
        resp = client.get("/api/logs?time=today")
        assert resp.status_code == 200

    def test_time_filter_7d(self, client):
        """time=7d。"""
        resp = client.get("/api/logs?time=7d")
        assert resp.status_code == 200

    def test_time_filter_30d(self, client):
        """time=30d。"""
        resp = client.get("/api/logs?time=30d")
        assert resp.status_code == 200

    def test_model_filter(self, client):
        """按 model 过滤。"""
        resp = client.get("/api/logs?model=test-model-a")
        data = resp.json()
        for item in data["items"]:
            assert item["model"] == "test-model-a"

    def test_source_filter(self, client):
        """按 source 过滤。"""
        resp = client.get("/api/logs?source=hermes")
        data = resp.json()
        for item in data["items"]:
            assert item["data_source"] == "hermes"

    def test_pagination(self, client):
        """分页参数 page, limit。"""
        resp = client.get("/api/logs?page=1&limit=2")
        data = resp.json()
        assert data["page"] == 1
        assert data["limit"] == 2
        assert len(data["items"]) <= 2
        assert data["total"] == 8

    def test_page_2(self, client):
        """第二页。"""
        resp = client.get("/api/logs?page=2&limit=3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2

    def test_page_out_of_range(self, client):
        """超出范围页码返回空 items。"""
        resp = client.get("/api/logs?page=999&limit=50")
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 8

    def test_invalid_page_defaults(self, client):
        """无效 page 参数（page=0）FastAPI 默认处理为 1。"""
        resp = client.get("/api/logs?page=0&limit=10")
        # FastAPI 默认会拒绝 ge=1 的参数为 0
        assert resp.status_code in (200, 422)

    def test_sort_by_timestamp_desc(self, client):
        """验证按时间戳降序排列。"""
        resp = client.get("/api/logs?limit=3")
        items = resp.json()["items"]
        timestamps = [item["timestamp"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/trends
# ═══════════════════════════════════════════════════════════════════

class TestTrendsEndpoint:
    """测试 /api/trends 端点。"""

    def test_returns_trends(self, client):
        """正常返回趋势数据。"""
        resp = client.get("/api/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_trends_keys(self, client):
        """验证字段名。"""
        resp = client.get("/api/trends")
        data = resp.json()
        for d in data:
            assert "date" in d
            assert "requests" in d
            assert "input" in d
            assert "output" in d

    def test_trends_by_source(self, client):
        """按 source 过滤。"""
        resp = client.get("/api/trends?source=claude")
        assert resp.status_code == 200

    def test_trends_by_model(self, client):
        """按 model 过滤。"""
        resp = client.get("/api/trends?model=test-model-a")
        assert resp.status_code == 200

    def test_trends_time_filter(self, client):
        """time 参数。"""
        resp = client.get("/api/trends?time=today")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/providers
# ═══════════════════════════════════════════════════════════════════

class TestProvidersEndpoint:
    """测试 /api/providers 端点。"""

    def test_returns_providers(self, client):
        """正常返回 provider 聚合。"""
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_providers_keys(self, client):
        """验证字段名。"""
        resp = client.get("/api/providers")
        data = resp.json()
        expected = {
            "provider", "request_count", "total_input_tokens",
            "total_output_tokens", "total_cache_read",
            "total_cache_creation", "total_cost",
            "success_rate", "avg_latency_ms", "models",
        }
        for p in data:
            assert expected.issubset(p.keys())

    def test_provider_names(self, client):
        """provider 名基于 extract_provider 提取。"""
        # extract_provider("test-model-a") → "test"
        resp = client.get("/api/providers")
        providers = {p["provider"] for p in resp.json()}
        assert "test" in providers  # test-model-a / test-model-b → "test"

    def test_success_rate_computation(self, client):
        """验证成功率计算。r4 是 500 错误。"""
        resp = client.get("/api/providers")
        for p in resp.json():
            if p["provider"] == "test":
                # r1,r2,r3,r5,r6,r7 → 6次200, r4→500. 成功率 6/7≈85.7
                assert p["request_count"] == 7
                assert p["success_rate"] == pytest.approx(85.7, abs=0.1)

    def test_avg_latency(self, client):
        """延迟统计。"""
        resp = client.get("/api/providers")
        for p in resp.json():
            if p["avg_latency_ms"] > 0:
                assert isinstance(p["avg_latency_ms"], (int, float))

    def test_filter_by_model(self, client):
        """按 model 过滤。"""
        resp = client.get("/api/providers?model=test-model-a")
        assert resp.status_code == 200

    def test_time_filter(self, client):
        """time 参数。"""
        resp = client.get("/api/providers?time=30d")
        assert resp.status_code == 200

    def test_models_includes_all(self, client):
        """models 字段包含该 provider 下的所有 model。"""
        resp = client.get("/api/providers")
        for p in resp.json():
            assert isinstance(p["models"], list)


# ═══════════════════════════════════════════════════════════════════
#  ROUTE: GET /api/pricing  +  PUT /api/pricing
# ═══════════════════════════════════════════════════════════════════

class TestPricingEndpoints:
    """测试 /api/pricing GET 与 PUT。"""

    def test_get_pricing(self, client):
        """GET /api/pricing 返回定价列表。"""
        resp = client.get("/api/pricing")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        for entry in data:
            assert "model" in entry
            assert "input_price" in entry
            assert "output_price" in entry

    def test_update_pricing(self, client):
        """PUT /api/pricing 更新定价。"""
        new_pricing = [
            {
                "model": "test-model-a",
                "input_price": 5.0,
                "output_price": 10.0,
                "cache_read_price": 0.5,
                "cache_write_price": 1.0,
            }
        ]
        resp = client.put("/api/pricing", json=new_pricing)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] == 1

    def test_update_pricing_multiple(self, client):
        """批量更新多个 model。"""
        new_pricing = [
            {"model": "model-x", "input_price": 1.0, "output_price": 2.0},
            {"model": "model-y", "input_price": 3.0, "output_price": 6.0},
        ]
        resp = client.put("/api/pricing", json=new_pricing)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["updated"] == 2

    def test_update_pricing_empty(self, client):
        """空数组更新。"""
        resp = client.put("/api/pricing", json=[])
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0

    def test_update_pricing_missing_field(self, client):
        """缺少必填字段返回 422。"""
        bad_entry = [{"model": "broken"}]  # 缺少 input_price, output_price
        resp = client.put("/api/pricing", json=bad_entry)
        assert resp.status_code == 422


class TestRefreshEndpoint:
    """测试 POST /api/refresh。"""

    def test_refresh_returns_ok(self, client):
        """refresh 应返回 ok。"""
        resp = client.post("/api/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True


# ═══════════════════════════════════════════════════════════════════
#  工具函数测试
# ═══════════════════════════════════════════════════════════════════

class TestHermesProxyPassthrough:
    """Hermes proxy compatibility routes."""

    def test_chat_completion_keeps_model_name(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=1,
            name="default",
            base_url="http://upstream.test/v1",
            api_key="real-key",
        )
        captured = {}

        async def fake_proxy_chat_json(
            upstream_url,
            headers,
            upstream_body,
            provider_arg,
            request_model,
            target_model,
            created_at,
            start_ts,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "headers": headers,
                    "upstream_body": upstream_body,
                    "request_model": request_model,
                    "target_model": target_model,
                    "provider": provider_arg,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_default_provider", lambda: provider)
        monkeypatch.setattr(srv, "get_active_mapping", lambda: {"target_model": "", "provider_id": 0})
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "hermes-config-model", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "http://upstream.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer real-key"
        assert captured["upstream_body"]["model"] == "hermes-config-model"
        assert captured["request_model"] == "hermes-config-model"
        assert captured["target_model"] == "hermes-config-model"
        assert captured["provider"] is provider

    def test_mimo_chat_uses_xiaomi_upstream_and_incoming_auth(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        ds_provider = SimpleNamespace(
            id=1,
            name="ds",
            base_url="http://deepseek.test/v1",
            api_key="deepseek-key",
        )
        captured = {}

        async def fake_proxy_chat_json(
            upstream_url,
            headers,
            upstream_body,
            provider_arg,
            request_model,
            target_model,
            created_at,
            start_ts,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "headers": headers,
                    "upstream_body": upstream_body,
                    "request_model": request_model,
                    "target_model": target_model,
                    "provider": provider_arg,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_default_provider", lambda: ds_provider)
        monkeypatch.setattr(srv, "get_provider_by_name", lambda name: None)
        monkeypatch.setattr(srv, "get_active_mapping", lambda: {"target_model": "deepseek-v4-flash", "provider_id": 1})
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer xiaomi-token"},
            json={"model": "mimo-v2.5", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "https://api.xiaomimimo.com/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer xiaomi-token"
        assert captured["upstream_body"]["model"] == "mimo-v2.5"
        assert captured["request_model"] == "mimo-v2.5"
        assert captured["target_model"] == "mimo-v2.5"
        assert captured["provider"].name == "mimo"

    def test_models_fallback_without_provider(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        monkeypatch.setattr(srv, "get_default_provider", lambda: None)

        resp = client.get("/v1/models")

        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)

    def test_api_show_fallback_without_provider(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        monkeypatch.setattr(srv, "get_default_provider", lambda: None)

        resp = client.post("/api/show", json={"name": "hermes-config-model"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "hermes-config-model"
        assert "tools" in data["capabilities"]


class TestUtilityFunctions:
    """测试 _get_records, _load_cache 等工具函数。"""

    def test_get_records_uses_cache(self, monkeypatch):
        """_get_records(force=False) 应使用缓存。"""
        from hermes_token_dash import server as srv
        from hermes_token_dash.models import TokenUsage

        # 预填缓存：构造两条测试记录
        record = TokenUsage(
            request_id="cache-test-1", model="test-model-a",
            input_tokens=100, output_tokens=50, cache_read=0,
            cache_creation=0,
            timestamp=datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc),
            data_source="claude",
        )
        srv._cache = [record, record]

        # 调用 _get_records 应该直接返回缓存
        records = srv._get_records(force=False)
        assert len(records) == 2
        assert records[0].model == "test-model-a"

    def test_get_records_force_reloads(self, monkeypatch):
        """_get_records(force=True) 应重新加载。"""
        from hermes_token_dash import server as srv
        from hermes_token_dash.models import TokenUsage

        # 先设一个旧缓存 (单条记录)
        srv._cache = [TokenUsage(
            request_id="old-1", model="old-model",
            input_tokens=1, output_tokens=1, cache_read=0,
            cache_creation=0,
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            data_source="claude",
        )]
        # force 模式应调用 _load_cache → 返回 8 条
        records = srv._get_records(force=True)
        assert len(records) == 8

    def test_get_records_empty_cache_auto_loads(self, monkeypatch):
        """_get_records 空缓存时自动加载。"""
        from hermes_token_dash import server as srv

        srv._cache = []
        records = srv._get_records()
        assert len(records) == 8

    def test_load_cache_populates_cache(self, monkeypatch):
        """_load_cache 填充全局缓存。"""
        from hermes_token_dash import server as srv

        srv._cache = []
        result = srv._load_cache()
        assert len(result) == 8
        assert len(srv._cache) == 8

    def test_load_cache_uses_proxy_database_only(self, monkeypatch):
        """_load_cache uses proxy logs as the only data source."""
        from hermes_token_dash import server as srv

        now = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
        proxy_record = TokenUsage(
            request_id="proxy-1",
            model="proxy-model",
            input_tokens=10,
            output_tokens=5,
            cache_read=0,
            cache_creation=0,
            timestamp=now,
            data_source="hermes",
        )
        monkeypatch.setattr(srv, "parse_proxy_request_logs", lambda: [proxy_record])

        srv._cache = []

        result = srv._load_cache()

        assert result == [proxy_record]
        assert srv._cache == [proxy_record]

    def test_index_prefix_match(self, client):
        """验证 / 匹配（不与其他路由冲突）。"""
        resp = client.get("/")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════
#  边界条件与错误处理
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界条件测试。"""

    def test_non_existent_route(self, client):
        """不存在的路由返回 404。"""
        resp = client.get("/api/nonexistent")
        assert resp.status_code == 404

    def test_multiple_filters_combined(self, client):
        """同时使用多个查询参数。"""
        resp = client.get("/api/logs?time=30d&model=test-model-a&source=claude&page=1&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["model"] == "test-model-a"
            assert item["data_source"] == "claude"

    def test_response_content_type(self, client):
        """所有 API 端点返回 JSON。"""
        api_routes = [
            "/api/models",
            "/api/stats",
            "/api/summary",
            "/api/logs",
            "/api/trends",
            "/api/providers",
            "/api/pricing",
        ]
        for route in api_routes:
            resp = client.get(route)
            assert resp.status_code == 200
            assert "application/json" in resp.headers.get("content-type", "")

    def test_empty_cache_graceful(self, monkeypatch, client):
        """缓存完全为空时 API 仍正常工作。"""
        from hermes_token_dash import server as srv

        srv._cache = []
        # 重载（但 mock 仍返回数据）
        resp = client.get("/api/summary")
        assert resp.status_code == 200
