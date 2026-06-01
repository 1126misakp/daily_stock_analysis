# -*- coding: utf-8 -*-
"""
P0b 单测：实时数据源优先级解析 _resolve_realtime_source_priority。

决策①动态优先级：检测到 TICKFLOW_API_KEY 时把 tickflow 前置；
无 key 不前置（保证「不配置也可运行」）；显式 REALTIME_SOURCE_PRIORITY 优先于一切。
TickFlow 排在 Tushare 之前（实时主力）。
"""

import os
import unittest
from unittest.mock import patch

from src.config import Config


class TestResolveRealtimeSourcePriority(unittest.TestCase):
    def _resolve(self, env: dict) -> str:
        with patch.dict(os.environ, env, clear=True):
            return Config._resolve_realtime_source_priority()

    def test_default_without_any_key(self) -> None:
        resolved = self._resolve({})
        self.assertEqual(resolved, "tencent,akshare_sina,efinance,akshare_em")
        self.assertNotIn("tickflow", resolved)

    def test_tickflow_prepended_when_key_present(self) -> None:
        resolved = self._resolve({"TICKFLOW_API_KEY": "tf-secret"})
        self.assertTrue(resolved.startswith("tickflow,"))
        self.assertEqual(
            resolved, "tickflow,tencent,akshare_sina,efinance,akshare_em"
        )

    def test_tickflow_before_tushare_when_both_present(self) -> None:
        resolved = self._resolve(
            {"TICKFLOW_API_KEY": "tf-secret", "TUSHARE_TOKEN": "ts-token"}
        )
        self.assertEqual(
            resolved,
            "tickflow,tushare,tencent,akshare_sina,efinance,akshare_em",
        )

    def test_tushare_only_unchanged(self) -> None:
        resolved = self._resolve({"TUSHARE_TOKEN": "ts-token"})
        self.assertEqual(
            resolved, "tushare,tencent,akshare_sina,efinance,akshare_em"
        )

    def test_explicit_priority_respected_over_tickflow(self) -> None:
        resolved = self._resolve(
            {
                "TICKFLOW_API_KEY": "tf-secret",
                "REALTIME_SOURCE_PRIORITY": "tencent,efinance",
            }
        )
        self.assertEqual(resolved, "tencent,efinance")


if __name__ == "__main__":
    unittest.main()
