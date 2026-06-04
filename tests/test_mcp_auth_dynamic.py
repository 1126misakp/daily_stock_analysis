# -*- coding: utf-8 -*-
"""动态鉴权：provider 切换后免重启即时生效；TTL 缓存与失效。"""
import asyncio
import unittest

from api.mcp.auth import MCPAuthMiddleware, _CachedKeyProvider


class _Spy:
    def __init__(self):
        self.called = False
    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _status(mw, token):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    scope = {"type": "http", "headers": headers}
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(msg):
        sent.append(msg)
    asyncio.run(mw(scope, receive, send))
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


class TestDynamicAuth(unittest.TestCase):
    def test_provider_switch_takes_effect(self):
        current = {"keys": {"old"}}
        mw = MCPAuthMiddleware(_Spy(), lambda: current["keys"])
        self.assertEqual(_status(mw, "old"), 200)
        current["keys"] = {"new"}           # 模拟重置
        self.assertEqual(_status(mw, "old"), 401)   # 旧 key 立即失效
        self.assertEqual(_status(mw, "new"), 200)   # 新 key 立即生效

    def test_empty_provider_denies(self):
        mw = MCPAuthMiddleware(_Spy(), lambda: set())
        self.assertEqual(_status(mw, "anything"), 401)


class TestCachedProvider(unittest.TestCase):
    def test_ttl_caches_then_refreshes(self):
        calls = {"n": 0}
        def loader():
            calls["n"] += 1
            return {f"k{calls['n']}"}
        p = _CachedKeyProvider(loader, ttl=10.0)
        self.assertEqual(p(), {"k1"})
        self.assertEqual(p(), {"k1"})        # 命中缓存，未再 load
        self.assertEqual(calls["n"], 1)
        p.invalidate()
        self.assertEqual(p(), {"k2"})        # 失效后重新 load
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
