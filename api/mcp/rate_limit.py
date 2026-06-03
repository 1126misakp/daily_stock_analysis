# -*- coding: utf-8 -*-
"""MCP 中转站限流扩展点。

本期不做限流（spec 决定）。保留 NoopRateLimiter 作为占位与插拔点：
将来要保护 Tushare 积分 / TickFlow 60次/分钟配额时，替换为令牌桶实现并在
api/mcp/server.py 的派发前调用 .allow(key) 即可，无需改动主路径结构。
"""
from __future__ import annotations


class NoopRateLimiter:
    """永远放行。占位实现。"""

    def allow(self, api_key: str) -> bool:
        return True
