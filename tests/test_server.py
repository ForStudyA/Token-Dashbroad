"""server.py 单元测试。

覆盖所有 9 个 API 端点、工具函数、边界条件与错误处理。
"""

from __future__ import annotations

import json
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
            should_log=True,
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

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 1 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "passthrough", "target_model": "", "provider_id": 1, "mapping_id": 0},
        )
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

    def test_chat_completion_requires_active_proxy(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "", "target_model": "", "provider_id": 0, "mapping_id": 0},
        )

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "hermes-config-model", "messages": [], "stream": False},
        )

        assert resp.status_code == 400
        assert "No enabled proxy provider configured" in resp.text

    def test_chat_completion_ignores_disabled_active_provider(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=1,
            name="disabled",
            base_url="http://disabled.test/v1",
            api_key="disabled-key",
            enabled=False,
        )

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 1 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "passthrough", "target_model": "", "provider_id": 1, "mapping_id": 0},
        )

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "hermes-config-model", "messages": [], "stream": False},
        )

        assert resp.status_code == 400
        assert "No enabled proxy provider configured" in resp.text

    def test_chat_completion_uses_active_mapping(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
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
            should_log=True,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "headers": headers,
                    "upstream_body": upstream_body,
                    "provider": provider_arg,
                    "request_model": request_model,
                    "target_model": target_model,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 2 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {
                "mode": "mapping",
                "target_model": "upstream-model",
                "provider_id": 2,
                "mapping_id": 9,
            },
        )
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "hermes-config-model", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "http://mapped.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer mapped-key"
        assert captured["upstream_body"]["model"] == "upstream-model"
        assert captured["request_model"] == "hermes-config-model"
        assert captured["target_model"] == "upstream-model"
        assert captured["provider"] is provider

    def test_active_mapping_uses_target_provider_key_not_request_auth(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
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
            should_log=True,
        ):
            captured.update({"headers": headers, "provider": provider_arg})
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 2 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {
                "mode": "mapping",
                "target_model": "deepseek-v4-pro",
                "provider_id": 2,
                "mapping_id": 9,
            },
        )
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer expired-xiaomi-key"},
            json={"model": "mimo-v2.5", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["headers"]["Authorization"] == "Bearer mapped-key"
        assert captured["provider"] is provider

    def test_disabled_proxy_forwards_without_logging(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

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
            should_log=True,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "request_model": request_model,
                    "target_model": target_model,
                    "should_log": should_log,
                    "provider": provider_arg,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_proxy_enabled", lambda: False)
        monkeypatch.setattr(srv, "get_provider_by_name", lambda name: None)
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer live-xiaomi-key"},
            json={"model": "mimo-v2.5", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "https://api.xiaomimimo.com/v1/chat/completions"
        assert captured["request_model"] == "mimo-v2.5"
        assert captured["target_model"] == "mimo-v2.5"
        assert captured["should_log"] is False
        assert captured["provider"].name == "mimo"

    def test_proxy_chat_json_logs_response_model(self, monkeypatch):
        import asyncio
        import json
        import urllib.request

        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
        )
        captured = {}

        class Headers:
            def get_content_type(self):
                return "application/json"

        class FakeResponse:
            status = 200
            headers = Headers()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({
                    "id": "resp-1",
                    "model": "actual-upstream-model",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                }).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=600: FakeResponse())
        monkeypatch.setattr(srv, "_load_cache", lambda: None)
        monkeypatch.setattr(srv, "insert_request_log", lambda **kwargs: captured.update(kwargs))

        resp = asyncio.run(
            srv._proxy_chat_json(
                "http://mapped.test/v1/chat/completions",
                {"Content-Type": "application/json"},
                {"model": "target-model", "messages": []},
                provider,
                "request-model",
                "target-model",
                123,
                1.0,
            )
        )

        assert resp.status_code == 200
        assert captured["request_model"] == "request-model"
        assert captured["model"] == "actual-upstream-model"

    def test_chat_completion_normalizes_responses_style_body(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
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
            should_log=True,
        ):
            captured["upstream_body"] = upstream_body
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 2 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "mapping", "target_model": "upstream-model", "provider_id": 2, "mapping_id": 9},
        )
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "request-model",
                "instructions": "Be brief.",
                "input": "Hello",
                "max_output_tokens": 33,
            },
        )

        assert resp.status_code == 200
        assert captured["upstream_body"]["model"] == "upstream-model"
        assert captured["upstream_body"]["messages"] == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hello"},
        ]
        assert captured["upstream_body"]["max_tokens"] == 33
        assert "input" not in captured["upstream_body"]
        assert "instructions" not in captured["upstream_body"]

    def test_provider_test_endpoint_reports_models(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=7,
            name="provider",
            base_url="http://provider.test/v1",
            api_key="key",
            enabled=True,
        )

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 7 else None)
        monkeypatch.setattr(
            srv,
            "_read_upstream_json",
            lambda url, provider, method="GET", body=None: (
                200,
                {"data": [{"id": "model-a"}, {"id": "model-b"}]},
                "",
            ),
        )

        resp = client.post("/api/proxy/providers/7/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["models"] == ["model-a", "model-b"]
        assert data["model_count"] == 2

    def test_provider_test_endpoint_rejects_empty_model_list(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=7,
            name="provider",
            base_url="http://provider.test/v1",
            api_key="key",
            enabled=True,
        )

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 7 else None)
        monkeypatch.setattr(
            srv,
            "_read_upstream_json",
            lambda url, provider, method="GET", body=None: (200, {"ok": True}, ""),
        )

        resp = client.post("/api/proxy/providers/7/test")

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["model_count"] == 0
        assert "模型列表" in data["message"]

    def test_active_models_endpoint_uses_active_provider(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=7,
            name="provider",
            base_url="http://provider.test/v1",
            api_key="key",
            enabled=True,
        )
        captured = {}

        def fake_upstream_get_json(url, provider_arg, fallback):
            captured["url"] = url
            captured["provider"] = provider_arg
            return JSONResponse({"data": [{"id": "model-a"}]})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 7 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "passthrough", "target_model": "", "provider_id": 7, "mapping_id": 0},
        )
        monkeypatch.setattr(srv, "_upstream_get_json", fake_upstream_get_json)

        resp = client.get("/v1/models")

        assert resp.status_code == 200
        assert captured["url"] == "http://provider.test/v1/models"
        assert captured["provider"] is provider
        assert resp.json()["data"][0]["id"] == "model-a"

    def test_active_proxy_api_is_exclusive(self, monkeypatch, tmp_path, client):
        from hermes_token_dash import proxy_db as pdb

        data_dir = tmp_path / "token-dashboard"
        monkeypatch.setattr(pdb, "DATA_DIR", data_dir)
        monkeypatch.setattr(pdb, "DB_PATH", data_dir / "token-dashboard.db")

        client.post(
            "/api/proxy/providers",
            json={"name": "p1", "base_url": "http://p1.test", "api_key": "k1", "enabled": True},
        )
        client.post(
            "/api/proxy/providers",
            json={"name": "p2", "base_url": "http://p2.test", "api_key": "k2", "enabled": True},
        )
        providers = client.get("/api/proxy/providers").json()["providers"]
        provider_ids = {p["name"]: p["id"] for p in providers}

        client.post(
            "/api/proxy/mappings",
            json={
                "source_model": "*",
                "target_model": "model-a",
                "provider_id": provider_ids["p1"],
                "enabled": True,
            },
        )
        mapping_a = next(
            m for m in client.get("/api/proxy/mappings").json()["mappings"]
            if m["target_model"] == "model-a"
        )
        active = client.get("/api/proxy/active-mapping").json()
        assert active["mode"] == ""

        client.post(
            "/api/proxy/mappings",
            json={
                "source_model": "*",
                "target_model": "model-b",
                "provider_id": provider_ids["p2"],
                "enabled": True,
            },
        )
        mappings = client.get("/api/proxy/mappings").json()["mappings"]
        mapping_b = next(m for m in mappings if m["target_model"] == "model-b")
        active = client.get("/api/proxy/active-mapping").json()

        assert active["mode"] == ""
        assert not [m for m in mappings if m["enabled"] and not m["protected"]]

        client.post(
            "/api/proxy/active-mapping",
            json={"mode": "mapping", "mapping_id": mapping_a["id"]},
        )
        mappings = client.get("/api/proxy/mappings").json()["mappings"]
        active = client.get("/api/proxy/active-mapping").json()
        assert active["mapping_id"] == mapping_a["id"]
        assert [m["id"] for m in mappings if m["enabled"] and not m["protected"]] == [mapping_a["id"]]

        client.post(
            "/api/proxy/active-mapping",
            json={"mode": "mapping", "mapping_id": mapping_b["id"]},
        )
        mappings = client.get("/api/proxy/mappings").json()["mappings"]
        active = client.get("/api/proxy/active-mapping").json()
        assert active["mode"] == "mapping"
        assert active["mapping_id"] == mapping_b["id"]
        assert active["provider_id"] == provider_ids["p2"]
        assert [m["id"] for m in mappings if m["enabled"] and not m["protected"]] == [mapping_b["id"]]

        client.post(f"/api/proxy/providers/{provider_ids['p2']}/toggle")
        active = client.get("/api/proxy/active-mapping").json()
        mappings = client.get("/api/proxy/mappings").json()["mappings"]
        assert active["mode"] == ""
        assert active["provider_id"] == 0
        assert not [m for m in mappings if m["enabled"] and not m["protected"]]

        client.post(
            "/api/proxy/active-mapping",
            json={"mode": "passthrough", "provider_id": provider_ids["p1"]},
        )
        mappings = client.get("/api/proxy/mappings").json()["mappings"]
        active = client.get("/api/proxy/active-mapping").json()
        assert active["mode"] == "passthrough"
        assert active["provider_id"] == provider_ids["p1"]
        assert not [m for m in mappings if m["enabled"] and not m["protected"]]

        client.delete(f"/api/proxy/providers/{provider_ids['p1']}")
        active = client.get("/api/proxy/active-mapping").json()
        assert active["mode"] == ""
        assert active["provider_id"] == 0

    def test_proxy_provider_and_active_mapping_are_agent_scoped(self, monkeypatch, tmp_path, client):
        from hermes_token_dash import proxy_db as pdb

        data_dir = tmp_path / "token-dashboard"
        monkeypatch.setattr(pdb, "DATA_DIR", data_dir)
        monkeypatch.setattr(pdb, "DB_PATH", data_dir / "token-dashboard.db")

        hermes_response = client.post(
            "/api/proxy/providers",
            json={
                "agent_name": "hermes",
                "name": "shared",
                "base_url": "http://hermes-provider.test/v1",
                "api_key": "hermes-key",
                "enabled": True,
            },
        )
        claude_response = client.post(
            "/api/proxy/providers",
            json={
                "agent_name": "claude_code",
                "name": "shared",
                "base_url": "http://claude-provider.test/v1",
                "api_key": "claude-key",
                "enabled": True,
            },
        )
        assert hermes_response.status_code == 200
        assert claude_response.status_code == 200

        hermes_providers = client.get("/api/proxy/providers?agent=hermes").json()["providers"]
        claude_providers = client.get("/api/proxy/providers?agent=claude_code").json()["providers"]
        assert [p["base_url"] for p in hermes_providers] == ["http://hermes-provider.test/v1"]
        assert [p["base_url"] for p in claude_providers] == ["http://claude-provider.test/v1"]

        claude_provider_id = claude_providers[0]["id"]
        client.post(
            "/api/proxy/active-mapping",
            json={
                "agent_name": "claude_code",
                "mode": "passthrough",
                "provider_id": claude_provider_id,
            },
        )

        hermes_active = client.get("/api/proxy/active-mapping?agent=hermes").json()
        claude_active = client.get("/api/proxy/active-mapping?agent=claude_code").json()
        assert hermes_active["mode"] == ""
        assert hermes_active["provider_id"] == 0
        assert claude_active["mode"] == "passthrough"
        assert claude_active["provider_id"] == claude_provider_id

    def test_stale_active_provider_id_cannot_cross_agent(self, monkeypatch, tmp_path):
        from hermes_token_dash import proxy_db as pdb
        from hermes_token_dash import server as srv

        data_dir = tmp_path / "token-dashboard"
        monkeypatch.setattr(pdb, "DATA_DIR", data_dir)
        monkeypatch.setattr(pdb, "DB_PATH", data_dir / "token-dashboard.db")

        pdb.upsert_provider(
            agent_name="hermes",
            name="hermes-only",
            base_url="http://hermes-provider.test/v1",
            api_key="hermes-key",
            enabled=True,
        )
        hermes_provider = pdb.get_provider_by_name("hermes-only", agent_name="hermes")
        with pdb.connect() as conn:
            ts = pdb.now_epoch()
            for key, value in {
                "active_proxy_mode_claude_code": "passthrough",
                "active_provider_id_claude_code": str(hermes_provider.id),
            }.items():
                conn.execute(
                    """
                    INSERT INTO proxy_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                    """,
                    (key, value, ts),
                )
            conn.commit()

        target_model, provider = srv._select_chat_provider("claude-3-sonnet", "", "claude_code")

        assert target_model == "claude-3-sonnet"
        assert provider is None

    def test_chat_completions_uses_hermes_agent_config(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        captured = {}
        provider = SimpleNamespace(
            id=1,
            agent_name="hermes",
            name="hermes-provider",
            base_url="http://hermes-provider.test/v1",
            api_key="key",
            enabled=True,
        )

        def fake_select(request_model, auth_header, agent_name="hermes"):
            captured["agent_name"] = agent_name
            return request_model, provider

        async def fake_proxy_chat_json(*args, **kwargs):
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "_select_chat_provider", fake_select)
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "request-model", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["agent_name"] == "hermes"

    def test_messages_uses_claude_code_agent_config(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        captured = {}
        provider = SimpleNamespace(
            id=2,
            agent_name="claude_code",
            name="claude-provider",
            base_url="http://claude-provider.test/v1",
            api_key="key",
            enabled=True,
        )

        def fake_select(request_model, auth_header, agent_name="hermes"):
            captured["agent_name"] = agent_name
            return request_model, provider

        async def fake_proxy_anthropic_json(*args, **kwargs):
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "_select_chat_provider", fake_select)
        monkeypatch.setattr(srv, "_proxy_anthropic_json", fake_proxy_anthropic_json)

        resp = client.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hi"}]},
        )

        assert resp.status_code == 200
        assert captured["agent_name"] == "claude_code"

    def test_active_mapping_can_remap_mimo_model(self, monkeypatch, client):
        """Active mappings take precedence over the MiMo compatibility fallback."""
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        ds_provider = SimpleNamespace(
            id=1,
            name="ds",
            base_url="http://deepseek.test/v1",
            api_key="deepseek-key",
            enabled=True,
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
            should_log=True,
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

        monkeypatch.setattr(srv, "get_provider_by_name", lambda name: None)
        monkeypatch.setattr(srv, "get_provider", lambda pid: ds_provider if pid == 1 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {
                "mode": "mapping",
                "target_model": "deepseek-v4-flash",
                "provider_id": 1,
                "mapping_id": 8,
            },
        )
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mimo-v2.5", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "http://deepseek.test/v1/chat/completions"
        assert captured["upstream_body"]["model"] == "deepseek-v4-flash"
        assert captured["request_model"] == "mimo-v2.5"
        assert captured["target_model"] == "deepseek-v4-flash"
        assert captured["provider"] is ds_provider

    def test_mimo_model_falls_back_to_xiaomi_without_active_proxy(self, monkeypatch, client):
        """MiMo compatibility still works when no explicit proxy route is active."""
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

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
            should_log=True,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "upstream_body": upstream_body,
                    "request_model": request_model,
                    "target_model": target_model,
                    "provider": provider_arg,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider_by_name", lambda name: None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "", "target_model": "", "provider_id": 0, "mapping_id": 0},
        )
        monkeypatch.setattr(srv, "_proxy_chat_json", fake_proxy_chat_json)

        resp = client.post(
            "/v1/chat/completions",
            json={"model": "mimo-v2.5", "messages": [], "stream": False},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "https://api.xiaomimimo.com/v1/chat/completions"
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


class TestClaudeCodeProxy:
    def test_messages_uses_active_mapping_and_converts_body(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
        )
        captured = {}

        async def fake_proxy_anthropic_json(
            upstream_url,
            headers,
            upstream_body,
            provider_arg,
            request_model,
            target_model,
            created_at,
            start_ts,
            should_log=True,
        ):
            captured.update(
                {
                    "upstream_url": upstream_url,
                    "headers": headers,
                    "upstream_body": upstream_body,
                    "provider": provider_arg,
                    "request_model": request_model,
                    "target_model": target_model,
                }
            )
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 2 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "mapping", "target_model": "upstream-model", "provider_id": 2, "mapping_id": 9},
        )
        monkeypatch.setattr(srv, "_proxy_anthropic_json", fake_proxy_anthropic_json)

        resp = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4",
                "system": "Be brief.",
                "messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
                "tools": [{"name": "lookup", "description": "Lookup", "input_schema": {"type": "object"}}],
                "max_tokens": 42,
            },
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "http://mapped.test/v1/chat/completions"
        assert captured["headers"]["Authorization"] == "Bearer mapped-key"
        assert captured["request_model"] == "claude-sonnet-4"
        assert captured["target_model"] == "upstream-model"
        assert captured["upstream_body"]["model"] == "upstream-model"
        assert captured["upstream_body"]["messages"] == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Hello"},
        ]
        assert captured["upstream_body"]["tools"][0]["function"]["name"] == "lookup"
        assert captured["upstream_body"]["max_tokens"] == 42

    def test_messages_passthrough_keeps_request_model(self, monkeypatch, client):
        from fastapi.responses import JSONResponse
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=3,
            name="passthrough",
            base_url="http://passthrough.test/v1",
            api_key="provider-key",
            enabled=True,
        )
        captured = {}

        async def fake_proxy_anthropic_json(
            upstream_url,
            headers,
            upstream_body,
            provider_arg,
            request_model,
            target_model,
            created_at,
            start_ts,
            should_log=True,
        ):
            captured.update({"upstream_url": upstream_url, "upstream_body": upstream_body, "target_model": target_model})
            return JSONResponse({"ok": True})

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 3 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "passthrough", "target_model": "", "provider_id": 3, "mapping_id": 0},
        )
        monkeypatch.setattr(srv, "_proxy_anthropic_json", fake_proxy_anthropic_json)

        resp = client.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hi"}]},
        )

        assert resp.status_code == 200
        assert captured["upstream_url"] == "http://passthrough.test/v1/chat/completions"
        assert captured["upstream_body"]["model"] == "claude-sonnet-4"
        assert captured["target_model"] == "claude-sonnet-4"

    def test_messages_ignores_disabled_active_provider(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=1,
            name="disabled",
            base_url="http://disabled.test/v1",
            api_key="disabled-key",
            enabled=False,
        )

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 1 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "passthrough", "target_model": "", "provider_id": 1, "mapping_id": 0},
        )

        resp = client.post(
            "/v1/messages",
            json={"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "Hi"}]},
        )

        assert resp.status_code == 400
        assert resp.json()["type"] == "error"

    def test_proxy_anthropic_json_converts_response_and_logs(self, monkeypatch):
        import asyncio
        import urllib.request
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(id=2, name="mapped", base_url="http://mapped.test/v1", api_key="key")
        captured = {}

        class FakeHeaders:
            def get_content_type(self):
                return "application/json"

        class FakeResponse:
            status = 200
            headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "id": "chatcmpl-1",
                        "model": "actual-model",
                        "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
                    }
                ).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=600: FakeResponse())
        monkeypatch.setattr(srv, "_load_cache", lambda: None)
        monkeypatch.setattr(srv, "insert_request_log", lambda **kwargs: captured.update(kwargs))

        resp = asyncio.run(
            srv._proxy_anthropic_json(
                "http://mapped.test/v1/chat/completions",
                {"Content-Type": "application/json"},
                {"model": "target-model", "messages": []},
                provider,
                "request-model",
                "target-model",
                123,
                1.0,
            )
        )

        assert resp.status_code == 200
        data = json.loads(resp.body)
        assert data["type"] == "message"
        assert data["content"] == [{"type": "text", "text": "Hello"}]
        assert data["usage"] == {"input_tokens": 11, "output_tokens": 7}
        assert captured["source_app"] == "claude"
        assert captured["endpoint"] == "/v1/messages"
        assert captured["model"] == "actual-model"

    def test_messages_stream_converts_openai_sse(self, monkeypatch, client):
        import urllib.request
        from hermes_token_dash import server as srv

        provider = SimpleNamespace(
            id=2,
            name="mapped",
            base_url="http://mapped.test/v1",
            api_key="mapped-key",
            enabled=True,
        )
        logs = {}

        class FakeStreamResponse:
            status = 200

            def __init__(self):
                self._chunks = [
                    b'data: {"id":"chatcmpl-1","model":"actual-model","choices":[{"delta":{"content":"Hel"}}]}\n\n',
                    b'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n',
                    b"data: [DONE]\n\n",
                ]

            def read(self, _size=-1):
                return self._chunks.pop(0) if self._chunks else b""

            def close(self):
                pass

        monkeypatch.setattr(srv, "get_provider", lambda pid: provider if pid == 2 else None)
        monkeypatch.setattr(
            srv,
            "get_active_mapping",
            lambda: {"mode": "mapping", "target_model": "actual-model", "provider_id": 2, "mapping_id": 9},
        )
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=600: FakeStreamResponse())
        monkeypatch.setattr(srv, "_load_cache", lambda: None)
        monkeypatch.setattr(srv, "insert_request_log", lambda **kwargs: logs.update(kwargs))

        resp = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4",
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": True,
            },
        )

        assert resp.status_code == 200
        assert "event: message_start" in resp.text
        assert "event: content_block_delta" in resp.text
        assert "Hel" in resp.text
        assert "lo" in resp.text
        assert "event: message_stop" in resp.text
        assert logs["source_app"] == "claude"
        assert logs["is_streaming"] is True
        assert logs["raw_usage"] == {"prompt_tokens": 3, "completion_tokens": 2}


class TestProxyAgents:
    def test_proxy_agents_endpoint_lists_registered_adapters(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        monkeypatch.setattr(
            srv,
            "_agent_statuses",
            lambda: [
                {
                    "name": "claude_code",
                    "display_name": "Claude Code",
                    "installed": True,
                    "config_path": "settings.json",
                    "current_base_url": "http://127.0.0.1:8765",
                    "proxied": True,
                }
            ],
        )

        resp = client.get("/api/proxy/agents")

        assert resp.status_code == 200
        assert resp.json()["agents"][0]["name"] == "claude_code"
        assert resp.json()["agents"][0]["proxied"] is True

    def test_proxy_agent_sync_endpoint_syncs_one_adapter(self, monkeypatch, client):
        from hermes_token_dash import server as srv

        captured = {}

        def fake_sync(agent_name, enabled):
            captured["agent_name"] = agent_name
            captured["enabled"] = enabled
            return {"ok": True, "name": agent_name, "proxied": enabled}

        monkeypatch.setattr(srv, "_sync_agent_config", fake_sync)

        resp = client.post("/api/proxy/agents/claude_code/sync", json={"enabled": True})

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert captured == {"agent_name": "claude_code", "enabled": True}


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
