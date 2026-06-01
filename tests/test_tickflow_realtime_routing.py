# -*- coding: utf-8 -*-
"""
P0b 单测：base.py 实时路由 source=="tickflow" 分支（机制②配置字符串路由）。

验证 get_realtime_quote 在 realtime_source_priority 含 tickflow 时，
经 _get_fetcher_by_name("TickFlowFetcher", capability="realtime_quote") 命中并派发。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote


def _make_quote(code: str = "600000") -> UnifiedRealtimeQuote:
    return UnifiedRealtimeQuote(
        code=code,
        name="浦发银行",
        source=RealtimeSource.TICKFLOW,
        price=9.37,
        change_pct=1.74,
    )


class TestTickFlowRealtimeRouting(unittest.TestCase):
    @patch("src.config.get_config")
    def test_a_share_realtime_routes_to_tickflow(self, mock_get_config) -> None:
        mock_get_config.return_value = SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority="tickflow,tencent,akshare_sina",
        )

        tickflow = MagicMock()
        tickflow.name = "TickFlowFetcher"
        tickflow.priority = -2
        tickflow.is_available_for_request.return_value = True
        tickflow.get_realtime_quote.return_value = _make_quote("600000")

        manager = DataFetcherManager(fetchers=[tickflow])

        quote = manager.get_realtime_quote("600000")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.source, RealtimeSource.TICKFLOW)
        tickflow.get_realtime_quote.assert_called_once_with("600000")

    @patch("src.config.get_config")
    def test_tickflow_skipped_when_not_in_priority(self, mock_get_config) -> None:
        """优先级不含 tickflow 时不应调用 TickFlow（即便已注册进链）。"""
        mock_get_config.return_value = SimpleNamespace(
            enable_realtime_quote=True,
            realtime_source_priority="tencent,akshare_sina",
        )

        tickflow = MagicMock()
        tickflow.name = "TickFlowFetcher"
        tickflow.priority = -2
        tickflow.is_available_for_request.return_value = True
        tickflow.get_realtime_quote.return_value = _make_quote("600000")

        manager = DataFetcherManager(fetchers=[tickflow])
        manager.get_realtime_quote("600000")

        tickflow.get_realtime_quote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
