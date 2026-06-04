# -*- coding: utf-8 -*-
"""MCP 中转站受管 API Key 的读取与重置。

单一受管 key，存于 data/.env 的 MCP_API_KEYS。重置整体覆盖（旧 key 一并失效），
写后显式 chmod 600（_atomic_upsert 的 os.replace 不保留权限）。
"""
from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from src.core.config_manager import ConfigManager
from api.mcp.auth import first_api_key

logger = logging.getLogger(__name__)

_KEY_ENV = "MCP_API_KEYS"  # 必须全大写：ConfigManager.apply_updates 会对写入键做 key.upper()
_MASK = "******"


class MCPKeyService:
    def __init__(self, manager: Optional[ConfigManager] = None):
        self._manager = manager or ConfigManager()

    def get_current_key(self) -> Optional[str]:
        raw = self._manager.read_config_map().get(_KEY_ENV, "")
        return first_api_key(raw)

    def reset_key(self) -> str:
        """生成新 key，整体覆盖 MCP_API_KEYS，保持文件 600，返回新 key。"""
        new_key = secrets.token_hex(24)
        self._manager.apply_updates(
            [(_KEY_ENV, new_key)],
            sensitive_keys={_KEY_ENV},
            mask_token=_MASK,
        )
        try:
            os.chmod(self._manager.env_path, 0o600)
        except OSError as exc:
            logger.warning("chmod 600 on %s failed: %s", self._manager.env_path, exc)
        return new_key
