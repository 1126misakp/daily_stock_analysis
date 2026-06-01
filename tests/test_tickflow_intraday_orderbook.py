# -*- coding: utf-8 -*-
"""
P2-2 单测：TickFlowFetcher 新增 分钟K(get_intraday_kline) + 五档盘口(get_order_book)，
以及 DataFetcherManager 对应委托方法（仅 A 股，TickFlow 专属能力）。

真实形状（实测 tickflow 0.1.22）：
- klines.get(period=5m/...) 列：symbol/name/timestamp/trade_date/trade_time/open/high/low/close/volume/amount
- depth.get(symbol) → dict：symbol/region/timestamp/bid_prices[5]/bid_volumes[5]/ask_prices[5]/ask_volumes[5]
"""

import unittest

import pandas as pd

from data_provider.tickflow_fetcher import TickFlowFetcher


def _sample_minute_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH"],
            "name": ["浦发银行", "浦发银行"],
            "timestamp": [1780037700000, 1780038000000],
            "trade_date": ["2026-05-29", "2026-05-29"],
            "trade_time": ["2026-05-29 14:55:00", "2026-05-29 15:00:00"],
            "open": [9.33, 9.35],
            "high": [9.36, 9.37],
            "low": [9.33, 9.34],
            "close": [9.36, 9.37],
            "volume": [43050, 101731],
            "amount": [40225702.0, 95298120.0],
        }
    )


_SAMPLE_DEPTH = {
    "symbol": "600000.SH",
    "region": "CN",
    "timestamp": 1780038004000,
    "bid_prices": [9.36, 9.35, 9.34, 9.33, 9.32],
    "bid_volumes": [1126, 2214, 2857, 3573, 3006],
    "ask_prices": [9.37, 9.38, 9.39, 9.4, 9.41],
    "ask_volumes": [5602, 14202, 8863, 8219, 3291],
}


class _FakeKlines:
    def __init__(self, df):
        self._df = df
        self.calls = []

    def get(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return self._df


class _FakeDepth:
    def __init__(self, depth):
        self._depth = depth
        self.calls = []

    def get(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return self._depth


class _FlakyKlines:
    """前 fail_times 次调用抛异常，之后返回 df。用于验证 W5 retry/降级。"""

    def __init__(self, df, fail_times):
        self._df = df
        self._fail_times = fail_times
        self.calls = 0

    def get(self, symbol, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ConnectionError("transient network error")
        return self._df


class _FlakyDepth:
    def __init__(self, depth, fail_times):
        self._depth = depth
        self._fail_times = fail_times
        self.calls = 0

    def get(self, symbol, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ConnectionError("transient network error")
        return self._depth


class _FakeClient:
    def __init__(self, df=None, depth=None):
        self.klines = _FakeKlines(df if df is not None else _sample_minute_df())
        self.depth = _FakeDepth(depth if depth is not None else dict(_SAMPLE_DEPTH))


class TestTickFlowIntraday(unittest.TestCase):
    def _fetcher(self, df=None):
        f = TickFlowFetcher(api_key="dummy-key")
        f._client = _FakeClient(df=df)
        return f

    def test_capability_probe_allows_intraday_and_orderbook(self):
        f = TickFlowFetcher(api_key="x")
        self.assertTrue(f.is_available_for_request("intraday_kline"))
        self.assertTrue(f.is_available_for_request("order_book"))

    def test_intraday_uses_suffixed_symbol_and_period(self):
        f = self._fetcher()
        f.get_intraday_kline("600000", period="15m", count=120)
        symbol, kwargs = f._client.klines.calls[0]
        self.assertEqual(symbol, "600000.SH")
        self.assertEqual(kwargs.get("period"), "15m")
        self.assertEqual(kwargs.get("count"), 120)
        self.assertTrue(kwargs.get("as_dataframe"))

    def test_intraday_normalizes_columns(self):
        f = self._fetcher()
        out = f.get_intraday_kline("600000", period="5m")
        self.assertEqual(
            list(out.columns),
            ["code", "datetime", "open", "high", "low", "close", "volume", "amount"],
        )
        self.assertEqual(out["code"].iloc[0], "600000")
        self.assertEqual(out["datetime"].iloc[0], "2026-05-29 14:55:00")
        self.assertEqual(out["close"].iloc[-1], 9.37)

    def test_intraday_rejects_invalid_period(self):
        f = self._fetcher()
        with self.assertRaises(ValueError):
            f.get_intraday_kline("600000", period="3m")

    def test_intraday_returns_none_for_non_a_share(self):
        f = self._fetcher()
        self.assertIsNone(f.get_intraday_kline("AAPL"))
        self.assertIsNone(f.get_intraday_kline("HK00700"))

    def test_intraday_returns_none_on_empty(self):
        f = self._fetcher(df=pd.DataFrame())
        self.assertIsNone(f.get_intraday_kline("600000"))

    def test_intraday_retries_then_succeeds(self):
        # W5：首次失败后重试一次成功，返回正常数据
        f = self._fetcher()
        f._client.klines = _FlakyKlines(_sample_minute_df(), fail_times=1)
        out = f.get_intraday_kline("600000", period="5m")
        self.assertIsNotNone(out)
        self.assertEqual(f._client.klines.calls, 2)
        self.assertEqual(out["close"].iloc[-1], 9.37)

    def test_intraday_degrades_after_two_failures(self):
        # W5：连续两次失败后降级返回 None（不向上抛异常）
        f = self._fetcher()
        f._client.klines = _FlakyKlines(_sample_minute_df(), fail_times=99)
        self.assertIsNone(f.get_intraday_kline("600000", period="5m"))
        self.assertEqual(f._client.klines.calls, 2)


class TestTickFlowOrderBook(unittest.TestCase):
    def _fetcher(self, depth=None):
        f = TickFlowFetcher(api_key="dummy-key")
        f._client = _FakeClient(depth=depth)
        return f

    def test_order_book_uses_suffixed_symbol(self):
        f = self._fetcher()
        f.get_order_book("600000")
        symbol, _ = f._client.depth.calls[0]
        self.assertEqual(symbol, "600000.SH")

    def test_order_book_maps_five_levels(self):
        f = self._fetcher()
        ob = f.get_order_book("600000")
        self.assertEqual(ob["code"], "600000")
        self.assertEqual(ob["timestamp"], 1780038004000)
        self.assertEqual(len(ob["bids"]), 5)
        self.assertEqual(len(ob["asks"]), 5)
        self.assertEqual(ob["bids"][0], {"price": 9.36, "volume": 1126})
        self.assertEqual(ob["asks"][0], {"price": 9.37, "volume": 5602})

    def test_order_book_returns_none_for_non_a_share(self):
        f = self._fetcher()
        self.assertIsNone(f.get_order_book("AAPL"))

    def test_order_book_returns_none_on_empty(self):
        f = self._fetcher(depth={})
        self.assertIsNone(f.get_order_book("600000"))

    def test_order_book_retries_then_succeeds(self):
        # W5：首次失败后重试一次成功
        f = self._fetcher()
        f._client.depth = _FlakyDepth(dict(_SAMPLE_DEPTH), fail_times=1)
        ob = f.get_order_book("600000")
        self.assertIsNotNone(ob)
        self.assertEqual(f._client.depth.calls, 2)
        self.assertEqual(len(ob["bids"]), 5)

    def test_order_book_degrades_after_two_failures(self):
        # W5：连续两次失败后降级返回 None
        f = self._fetcher()
        f._client.depth = _FlakyDepth(dict(_SAMPLE_DEPTH), fail_times=99)
        self.assertIsNone(f.get_order_book("600000"))
        self.assertEqual(f._client.depth.calls, 2)


class TestManagerDelegation(unittest.TestCase):
    def _manager_with_tickflow(self):
        from data_provider.base import DataFetcherManager

        tickflow = TickFlowFetcher(api_key="dummy-key")
        tickflow._client = _FakeClient()
        return DataFetcherManager(fetchers=[tickflow]), tickflow

    def test_manager_intraday_delegates_to_tickflow(self):
        mgr, _ = self._manager_with_tickflow()
        out = mgr.get_intraday_kline("600000", period="5m")
        self.assertIsInstance(out, pd.DataFrame)
        self.assertIn("datetime", out.columns)

    def test_manager_order_book_delegates_to_tickflow(self):
        mgr, _ = self._manager_with_tickflow()
        ob = mgr.get_order_book("600000")
        self.assertEqual(ob["code"], "600000")
        self.assertEqual(len(ob["bids"]), 5)

    def test_manager_returns_none_for_non_cn(self):
        mgr, _ = self._manager_with_tickflow()
        self.assertIsNone(mgr.get_intraday_kline("AAPL"))
        self.assertIsNone(mgr.get_order_book("HK00700"))

    def test_manager_returns_none_without_tickflow(self):
        from data_provider.base import DataFetcherManager

        # 注：空列表会被构造器当作 falsy 而回退默认数据源初始化，
        # 故注入一个非 TickFlow 的占位 fetcher，确保链中无 TickFlowFetcher。
        class _Dummy:
            name = "DummyFetcher"
            priority = 0

        mgr = DataFetcherManager(fetchers=[_Dummy()])
        self.assertIsNone(mgr.get_intraday_kline("600000"))
        self.assertIsNone(mgr.get_order_book("600000"))


if __name__ == "__main__":
    unittest.main()
