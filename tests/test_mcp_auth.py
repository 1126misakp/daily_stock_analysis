# -*- coding: utf-8 -*-
"""MCP API Key 鉴权中间件：无/错 key → 401；正确 key → 放行；未配置 → 拒绝。"""
import asyncio
import unittest

from api.mcp.auth import MCPAuthMiddleware, parse_api_keys


class _Spy:
    def __init__(self):
        self.called = False
    async def __call__(self, scope, receive, send):
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(mw, headers):
    scope = {"type": "http", "headers": headers}
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(msg):
        sent.append(msg)
    asyncio.run(mw(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status


class TestParseKeys(unittest.TestCase):
    def test_parse_with_labels_and_plain(self):
        self.assertEqual(parse_api_keys("k1:alice, k2 ,"), {"k1", "k2"})
        self.assertEqual(parse_api_keys(""), set())


class TestAuthMiddleware(unittest.TestCase):
    def test_valid_key_passes(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [(b"authorization", b"Bearer secret1")])
        self.assertEqual(status, 200)
        self.assertTrue(spy.called)

    def test_missing_key_rejected(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [])
        self.assertEqual(status, 401)
        self.assertFalse(spy.called)

    def test_wrong_key_rejected(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, {"secret1"})
        status = _run(mw, [(b"authorization", b"Bearer nope")])
        self.assertEqual(status, 401)

    def test_unconfigured_denies_all(self):
        spy = _Spy()
        mw = MCPAuthMiddleware(spy, set())
        status = _run(mw, [(b"authorization", b"Bearer anything")])
        self.assertEqual(status, 401)
        self.assertFalse(spy.called)


if __name__ == "__main__":
    unittest.main()
