# -*- coding: utf-8 -*-
"""MCP 中转站 API Key 鉴权（ASGI 中间件）。

请求头 Authorization: Bearer <key>。Key 集合来自配置 mcp_api_keys，
格式 "key1:label1,key2:label2"（label 仅用于人读，鉴权只比对 key）。
未配置任何 key → 一律拒绝（安全默认，避免裸奔）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional, Set

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
    """包裹 MCP ASGI 子应用，做 Bearer key 校验。

    key 由 key_provider() 每请求实时提供（而非构造时冻结），以支持重置后免重启生效。
    """

    def __init__(self, app, key_provider: "Callable[[], Set[str]]"):
        self.app = app
        self.key_provider = key_provider

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        keys = self.key_provider() or set()
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization", b"").decode("latin-1")
        token = raw[7:].strip() if raw[:7].lower() == "bearer " else ""
        if not keys or token not in keys:
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    async def _reject(self, send):
        body = json.dumps({"error": "unauthorized"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


def load_mcp_api_keys_fresh(env_path=None) -> Set[str]:
    """实时从 data/.env 文件读取 MCP_API_KEYS（不走 Config 单例/os.environ，
    以便重置写文件后即时反映）。"""
    from src.core.config_manager import ConfigManager
    manager = ConfigManager(env_path=env_path) if env_path else ConfigManager()
    raw = manager.read_config_map().get("MCP_API_KEYS", "")
    return parse_api_keys(raw)


class _CachedKeyProvider:
    """带 TTL 的 key provider：每 TTL 秒最多读一次源，避免每请求读盘。"""

    def __init__(self, loader: "Callable[[], Set[str]]", ttl: float = 3.0):
        self._loader = loader
        self._ttl = ttl
        self._cached: Set[str] = set()
        self._ts = 0.0

    def __call__(self) -> Set[str]:
        now = time.monotonic()
        if self._ts == 0.0 or (now - self._ts) >= self._ttl:
            self._cached = self._loader()
            self._ts = now
        return self._cached

    def invalidate(self) -> None:
        self._ts = 0.0


_default_key_provider: "Optional[_CachedKeyProvider]" = None


def get_key_provider() -> _CachedKeyProvider:
    """进程级单例 provider（鉴权中间件与重置端点共用，便于重置后失效缓存）。"""
    global _default_key_provider
    if _default_key_provider is None:
        _default_key_provider = _CachedKeyProvider(load_mcp_api_keys_fresh)
    return _default_key_provider
