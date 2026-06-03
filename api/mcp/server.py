# -*- coding: utf-8 -*-
"""MCP 工具中转站 server 装配。

- build_mcp_registry(): 白名单注册表（镜像 14 + 新增 16 = 30），排除私有/回测工具。
- 低层 mcp Server：list_tools 反射注册表，call_tool 按名字派发到 handler。
- StreamableHTTPSessionManager：单例，供 lifespan run() 与挂载 handle_request 共用。
- build_mcp_asgi_app(): 返回带 API Key 鉴权的 ASGI 子应用，供 app.mount("/mcp", ...)。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool

from src.agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 私有数据 + 回测工具排除（回测工具本就不在镜像列表里，这里再显式兜底）
EXCLUDED_TOOL_NAMES = {
    "get_portfolio_snapshot",
    "get_analysis_context",
    "get_stock_backtest_summary",
    "get_skill_backtest_summary",
    "get_strategy_backtest_summary",
}


def build_mcp_registry() -> ToolRegistry:
    """构建对外开放的白名单注册表（恰好 30 个工具）。"""
    from src.agent.tools.data_tools import ALL_DATA_TOOLS
    from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
    from src.agent.tools.market_tools import ALL_MARKET_TOOLS
    from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
    from src.agent.tools.dataset_tools import ALL_DATASET_TOOLS

    registry = ToolRegistry()
    for tool_def in (ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_MARKET_TOOLS
                     + ALL_SEARCH_TOOLS + ALL_DATASET_TOOLS):
        if tool_def.name in EXCLUDED_TOOL_NAMES:
            continue
        registry.register(tool_def)
    return registry


def _dispatch(registry: ToolRegistry, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """同步派发（可单测）：执行工具 handler，错误转结构化 dict。

    先用 __contains__ 判断工具是否存在（避免 handler 内部自抛 KeyError 被误判为
    'Unknown tool'），再 try/except 包执行。"""
    if name not in registry:
        return {"error": f"Unknown tool: {name}"}
    try:
        return registry.execute(name, **(arguments or {}))
    except Exception:
        logger.warning(f"[mcp] tool '{name}' execution failed", exc_info=True)
        return {"error": f"Tool execution failed: {name}"}


# ---------- 单例装配 ----------

_registry: Optional[ToolRegistry] = None
_server: Optional[Server] = None
_session_manager: Optional[StreamableHTTPSessionManager] = None


def _get_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = build_mcp_registry()
    return _registry


def _build_server() -> Server:
    server = Server("a-stock-tool-gateway")
    registry = _get_registry()

    @server.list_tools()
    async def _list_tools() -> List[types.Tool]:
        return [types.Tool(**d) for d in registry.to_mcp_tools()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: Dict[str, Any]) -> List[types.TextContent]:
        result = await run_in_threadpool(_dispatch, registry, name, arguments)
        text = json.dumps(result, ensure_ascii=False, default=str)
        return [types.TextContent(type="text", text=text)]

    return server


def _security_settings() -> Optional[TransportSecuritySettings]:
    """默认关闭 DNS rebinding 保护（部署在受信 Nginx 后，且有 API Key 网关）；
    可经 config 打开并配置 allowed_hosts/origins。"""
    from src.config import get_config
    cfg = get_config()
    enabled = bool(getattr(cfg, "mcp_dns_rebinding_protection", False))
    if not enabled:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    hosts = [h for h in str(getattr(cfg, "mcp_allowed_hosts", "")).split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts, allowed_origins=hosts,
    )


def get_mcp_session_manager() -> StreamableHTTPSessionManager:
    """单例 SessionManager（lifespan run() 与挂载 handle_request 共用同一个）。"""
    global _server, _session_manager
    if _session_manager is None:
        _server = _build_server()
        _session_manager = StreamableHTTPSessionManager(
            app=_server,
            event_store=None,
            json_response=True,   # 返回普通 JSON，规避 Cloudflare 对长连 SSE 的限制
            stateless=True,       # 网关无状态：每请求独立处理，利于反代/无会话保持
            security_settings=_security_settings(),
        )
    return _session_manager


def build_mcp_asgi_app():
    """返回带 API Key 鉴权的 ASGI 子应用，供 app.mount('/mcp', ...)。"""
    from api.mcp.auth import MCPAuthMiddleware, load_mcp_api_keys

    async def handle_streamable_http(scope, receive, send):
        await get_mcp_session_manager().handle_request(scope, receive, send)

    return MCPAuthMiddleware(handle_streamable_http, load_mcp_api_keys())
