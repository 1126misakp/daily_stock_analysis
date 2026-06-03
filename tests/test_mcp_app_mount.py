# -*- coding: utf-8 -*-
"""确认 create_app 挂载了 /mcp 子应用。"""
import os
import unittest

from api.app import create_app


class TestMcpMount(unittest.TestCase):
    def test_mcp_route_mounted(self):
        app = create_app()
        mounted = [getattr(r, "path", "") for r in app.routes]
        self.assertTrue(any(str(p).startswith("/mcp") for p in mounted),
                        f"/mcp not mounted; routes={mounted}")


class TestMcpInitializeE2E(unittest.TestCase):
    """真实 MCP initialize 穿过 归一化中间件 + auth + mount + BaseHTTPMiddleware +
    json_response 全链路。

    用 httpx ASGITransport + 单任务内进出 lifespan_context（asyncio.run），既真实驱动
    startup→请求→shutdown 的完整链路（与 uvicorn 单任务一致），又规避本仓库 conftest 自定义
    TestClient 在 __enter__/__exit__ 用两次 run_until_complete 跨任务进出 lifespan、触发
    StreamableHTTPSessionManager.run() 'cancel scope in a different task' 的夹具局限。"""

    def test_initialize_through_full_chain(self):
        import asyncio
        from unittest.mock import patch
        import httpx
        from mcp.types import LATEST_PROTOCOL_VERSION
        from src.config import Config

        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"}}}
        hdr = {"Authorization": "Bearer testkey",
               "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
        no_key_hdr = {"Accept": "application/json, text/event-stream",
                      "Content-Type": "application/json"}

        async def _drive():
            app = create_app()  # 调用时 build_mcp_asgi_app() 读到 testkey
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                             follow_redirects=False) as client:
                    # 无尾斜杠 /mcp 经归一化中间件命中 MCP 子应用（非 405）
                    r = await client.post("/mcp", json=init, headers=hdr)
                    r_slash = await client.post("/mcp/", json=init, headers=hdr)
                    # 无 key 必须被鉴权中间件拦成 401（响应体为中间件固定体）
                    r401 = await client.post("/mcp", json=init, headers=no_key_hdr)
                    return r, r_slash, r401

        with patch.dict(os.environ, {"MCP_API_KEYS": "testkey"}):
            Config.reset_instance()
            try:
                r, r_slash, r401 = asyncio.run(_drive())
            finally:
                Config.reset_instance()

        self.assertEqual(r.status_code, 200, f"/mcp chain broke: {r.status_code} {r.text[:300]}")
        self.assertTrue("jsonrpc" in r.text or "result" in r.text, r.text[:300])
        self.assertEqual(r_slash.status_code, 200, f"/mcp/ chain broke: {r_slash.status_code}")
        self.assertEqual(r401.status_code, 401, f"no-key should be 401, got {r401.status_code}")
        self.assertIn("unauthorized", r401.text)


if __name__ == "__main__":
    unittest.main()
