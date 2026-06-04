# -*- coding: utf-8 -*-
"""mcp-keys 端点：GET 返回当前 key/端点；POST reset 生成新 key 并失效鉴权缓存。"""
import unittest
from unittest.mock import patch, MagicMock

import api.v1.endpoints.mcp_keys as ep


class TestMcpKeysEndpoint(unittest.TestCase):
    def _request(self, host="a-stock.tech-monthly.online", proto="https"):
        req = MagicMock()
        req.headers = {"host": host, "x-forwarded-proto": proto}
        req.url.scheme = "http"
        return req

    def test_get_returns_key_and_endpoint(self):
        svc = MagicMock(); svc.get_current_key.return_value = "abc123"
        with patch.object(ep, "MCPKeyService", return_value=svc):
            out = ep.get_mcp_key(self._request())
        self.assertEqual(out["key"], "abc123")
        self.assertTrue(out["configured"])
        self.assertEqual(out["endpoint"], "https://a-stock.tech-monthly.online/mcp")

    def test_get_unconfigured(self):
        svc = MagicMock(); svc.get_current_key.return_value = None
        with patch.object(ep, "MCPKeyService", return_value=svc):
            out = ep.get_mcp_key(self._request())
        self.assertIsNone(out["key"])
        self.assertFalse(out["configured"])

    def test_reset_generates_and_invalidates_cache(self):
        svc = MagicMock(); svc.reset_key.return_value = "newkey"
        provider = MagicMock()
        with patch.object(ep, "MCPKeyService", return_value=svc), \
             patch("api.mcp.auth.get_key_provider", return_value=provider):
            out = ep.reset_mcp_key(self._request())
        self.assertEqual(out["key"], "newkey")
        self.assertTrue(out["configured"])
        svc.reset_key.assert_called_once()
        provider.invalidate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
