# -*- coding: utf-8 -*-
"""
P0b 单测：TickFlowFetcher 实时报价能力（mock SDK，离线确定性）。

覆盖：
- is_available_for_request 放行 "realtime_quote"（实时路由经 _get_fetcher_by_name 探针）
- get_realtime_quote 用带交易所后缀的 symbol 调 quotes.get(symbols=[...])
- 字段映射成 UnifiedRealtimeQuote（ext 比率 → 百分比；source=TICKFLOW）
- 非 A 股代码（美股/港股）直接返回 None，不发起请求
- 空行情返回 None
"""

import unittest

from data_provider.realtime_types import RealtimeSource, UnifiedRealtimeQuote
from data_provider.tickflow_fetcher import TickFlowFetcher


def _sample_quote_dict() -> dict:
    """模拟 TickFlow quotes.get(symbols=[...]) 返回的单条行情。

    ext 内 change_pct/amplitude/turnover_rate 为**比率**（与 get_main_indices 同口径）。
    """
    return {
        "symbol": "600000.SH",
        "last_price": 9.37,
        "prev_close": 9.21,
        "open": 9.18,
        "high": 9.38,
        "low": 9.13,
        "volume": 933303,
        "amount": 8.692213e8,
        "ext": {
            "name": "浦发银行",
            "change_pct": 0.01737,
            "change_amount": 0.16,
            "amplitude": 0.02715,
            "turnover_rate": 0.0031,
        },
    }


class _FakeQuotes:
    def __init__(self, quotes) -> None:
        self._quotes = quotes
        self.calls = []

    def get(self, symbols=None, universes=None, **kwargs):
        self.calls.append({"symbols": symbols, "universes": universes, **kwargs})
        return self._quotes


class _FakeClient:
    def __init__(self, quotes) -> None:
        self.quotes = _FakeQuotes(quotes)


class TestTickFlowRealtimeFetcher(unittest.TestCase):
    def _fetcher_with_fake(self, quotes=None):
        fetcher = TickFlowFetcher(api_key="dummy-key")
        if quotes is None:
            quotes = [_sample_quote_dict()]
        fetcher._client = _FakeClient(quotes)
        return fetcher

    def test_is_available_for_request_allows_realtime_quote(self) -> None:
        self.assertTrue(
            TickFlowFetcher(api_key="x").is_available_for_request("realtime_quote")
        )
        self.assertFalse(
            TickFlowFetcher(api_key="").is_available_for_request("realtime_quote")
        )

    def test_get_realtime_quote_uses_suffixed_symbol(self) -> None:
        fetcher = self._fetcher_with_fake()
        fetcher.get_realtime_quote("600000")

        call = fetcher._client.quotes.calls[0]
        self.assertEqual(call["symbols"], ["600000.SH"])

    def test_get_realtime_quote_maps_fields(self) -> None:
        fetcher = self._fetcher_with_fake()
        quote = fetcher.get_realtime_quote("600000")

        self.assertIsInstance(quote, UnifiedRealtimeQuote)
        self.assertEqual(quote.code, "600000")
        self.assertEqual(quote.name, "浦发银行")
        self.assertEqual(quote.source, RealtimeSource.TICKFLOW)
        self.assertAlmostEqual(quote.price, 9.37)
        self.assertAlmostEqual(quote.pre_close, 9.21)
        self.assertAlmostEqual(quote.open_price, 9.18)
        self.assertAlmostEqual(quote.high, 9.38)
        self.assertAlmostEqual(quote.low, 9.13)
        self.assertEqual(quote.volume, 933303)
        self.assertAlmostEqual(quote.amount, 8.692213e8)
        # ext 比率 → 百分比
        self.assertAlmostEqual(quote.change_pct, 1.737, places=3)
        self.assertAlmostEqual(quote.change_amount, 0.16)
        self.assertAlmostEqual(quote.turnover_rate, 0.31, places=3)
        self.assertAlmostEqual(quote.amplitude, 2.715, places=3)

    def test_get_realtime_quote_has_basic_data(self) -> None:
        fetcher = self._fetcher_with_fake()
        quote = fetcher.get_realtime_quote("600000")
        self.assertTrue(quote.has_basic_data())

    def test_get_realtime_quote_rejects_non_a_share(self) -> None:
        fetcher = self._fetcher_with_fake()
        self.assertIsNone(fetcher.get_realtime_quote("AAPL"))
        self.assertIsNone(fetcher.get_realtime_quote("HK00700"))
        # 非 A 股不应触发任何 SDK 调用
        self.assertEqual(fetcher._client.quotes.calls, [])

    def test_get_realtime_quote_empty_returns_none(self) -> None:
        fetcher = self._fetcher_with_fake(quotes=[])
        self.assertIsNone(fetcher.get_realtime_quote("600000"))

    def test_get_realtime_quote_no_api_key_returns_none(self) -> None:
        fetcher = TickFlowFetcher(api_key="")
        self.assertIsNone(fetcher.get_realtime_quote("600000"))


if __name__ == "__main__":
    unittest.main()
