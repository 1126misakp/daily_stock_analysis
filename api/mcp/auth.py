# -*- coding: utf-8 -*-
"""MCP 中转站 API Key 鉴权（ASGI 中间件）。

请求头 Authorization: Bearer <key>。Key 集合来自配置 mcp_api_keys，
格式 "key1:label1,key2:label2"（label 仅用于人读，鉴权只比对 key）。
未配置任何 key → 一律拒绝（安全默认，避免裸奔）。
"""
from __future__ import annotations

import json
import logging
from typing import Set

logger = logging.getLogger(__name__)


def parse_api_keys(raw: str) -> Set[str]:
    keys: Set[str] = set()
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key = item.split(":", 1)[0].strip()  # 去掉 :label
        if key:
            keys.add(key)
    return keys


def first_api_key(raw: str) -> "str | None":
    """Return the first key (order-preserving) from a 'k1:l1,k2:l2' string, or None."""
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        key = item.split(":", 1)[0].strip()
        if key:
            return key
    return None


def load_mcp_api_keys() -> Set[str]:
    from src.config import get_config
    cfg = get_config()
    return parse_api_keys(str(getattr(cfg, "mcp_api_keys", "") or ""))


class MCPAuthMiddleware:
    """包裹 MCP ASGI 子应用，做 Bearer key 校验。"""

    def __init__(self, app, api_keys: Set[str]):
        self.app = app
        self.api_keys = set(api_keys)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not self.api_keys or token not in self.api_keys:
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send):
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})
