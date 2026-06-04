# -*- coding: utf-8 -*-
"""MCP 受管 API Key 端点（登录态保护，挂 /api/v1/mcp-keys）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from src.services.mcp_key_service import MCPKeyService

router = APIRouter()


def _endpoint(request: Request) -> str:
    host = request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}/mcp"


@router.get("")
def get_mcp_key(request: Request) -> dict:
    key = MCPKeyService().get_current_key()
    return {"key": key, "endpoint": _endpoint(request), "configured": key is not None}


@router.post("/reset")
def reset_mcp_key(request: Request) -> dict:
    new_key = MCPKeyService().reset_key()
    from api.mcp.auth import get_key_provider
    get_key_provider().invalidate()
    return {"key": new_key, "endpoint": _endpoint(request), "configured": True}
