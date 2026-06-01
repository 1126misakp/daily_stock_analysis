# -*- coding: utf-8 -*-
"""
P0a 单测：TickFlowFetcher 日K 能力（mock SDK，离线确定性）。

覆盖：
- _fetch_raw_data 用带交易所后缀的 symbol 调 klines.get（period=1d/adjust=forward/区间）
- _normalize_data 输出严格等于日K标准 9 列，且 pct_chg 自算（首行为空）
- is_available_for_request 按 api_key 有无返回
- 非 A 股代码（美股/港股）被拒
"""

import unittest

import pandas as pd

from data_provider.base import DataFetchError
from data_provider.tickflow_fetcher import TickFlowFetcher


def _sample_tickflow_df() -> pd.DataFrame:
    """模拟 TickFlow klines.get(as_dataframe=True) 的返回列。"""
    return pd.DataFrame(
        {
            "symbol": ["600000.SH", "600000.SH", "600000.SH"],
            "name": ["浦发银行", "浦发银行", "浦发银行"],
            "timestamp": [1779811200000, 1779897600000, 1779984000000],
            "trade_date": ["2026-05-27", "2026-05-28", "2026-05-29"],
            "trade_time": ["2026-05-27 00:00:00", "2026-05-28 00:00:00", "2026-05-29 00:00:00"],
            "open": [9.29, 9.42, 9.18],
            "high": [9.54, 9.46, 9.38],
            "low": [9.26, 9.14, 9.13],
            "close": [9.43, 9.21, 9.37],
            "volume": [1345490, 836655, 933303],
            "amount": [1.269925e9, 7.763202e8, 8.692213e8],
        }
    )


class _FakeKlines:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.calls = []

    def get(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return self._df


class _FakeClient:
    def __init__(self, df: pd.DataFrame) -> None:
        self.klines = _FakeKlines(df)


class TestTickFlowDailyFetcher(unittest.TestCase):
    def _fetcher_with_fake(self, df=None):
        fetcher = TickFlowFetcher(api_key="dummy-key")
        fetcher._client = _FakeClient(df if df is not None else _sample_tickflow_df())
        return fetcher

    def test_priority_default_minus_two(self) -> None:
        self.assertEqual(TickFlowFetcher(api_key="x").priority, -2)

    def test_is_available_requires_api_key(self) -> None:
        self.assertTrue(TickFlowFetcher(api_key="x").is_available_for_request("daily_data"))
        self.assertFalse(TickFlowFetcher(api_key="").is_available_for_request("daily_data"))

    def test_fetch_raw_data_uses_suffixed_symbol_and_daily_params(self) -> None:
        fetcher = self._fetcher_with_fake()
        fetcher._fetch_raw_data("600000", "2026-05-20", "2026-05-29")

        symbol, kwargs = fetcher._client.klines.calls[0]
        self.assertEqual(symbol, "600000.SH")
        self.assertEqual(kwargs.get("period"), "1d")
        self.assertEqual(kwargs.get("adjust"), "forward")
        self.assertTrue(kwargs.get("as_dataframe"))
        self.assertIn("start_time", kwargs)
        self.assertIn("end_time", kwargs)
        self.assertIsInstance(kwargs["start_time"], int)
        self.assertLessEqual(kwargs["start_time"], kwargs["end_time"])

    def test_fetch_raw_data_rejects_non_a_share(self) -> None:
        fetcher = self._fetcher_with_fake()
        with self.assertRaises(DataFetchError):
            fetcher._fetch_raw_data("AAPL", "2026-05-20", "2026-05-29")
        with self.assertRaises(DataFetchError):
            fetcher._fetch_raw_data("HK00700", "2026-05-20", "2026-05-29")

    def test_normalize_data_outputs_standard_nine_columns(self) -> None:
        fetcher = self._fetcher_with_fake()
        out = fetcher._normalize_data(_sample_tickflow_df(), "600000")

        self.assertEqual(
            list(out.columns),
            ["code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg"],
        )
        self.assertEqual(out["code"].iloc[0], "600000")
        self.assertEqual(str(out["date"].iloc[0]), "2026-05-27")

    def test_normalize_data_computes_pct_chg(self) -> None:
        fetcher = self._fetcher_with_fake()
        out = fetcher._normalize_data(_sample_tickflow_df(), "600000")

        # 首行无前值 → 空
        self.assertTrue(pd.isna(out["pct_chg"].iloc[0]))
        # 第二行: (9.21-9.43)/9.43*100 ≈ -2.333
        self.assertAlmostEqual(out["pct_chg"].iloc[1], (9.21 - 9.43) / 9.43 * 100, places=3)

    def test_end_to_end_get_daily_data_source_via_template(self) -> None:
        """走 BaseFetcher.get_daily_data 模板，最终 df 含标准列与技术指标。"""
        fetcher = self._fetcher_with_fake()
        df = fetcher.get_daily_data("600000", start_date="2026-05-20", end_date="2026-05-29")
        self.assertFalse(df.empty)
        for col in ("date", "open", "high", "low", "close", "volume", "amount"):
            self.assertIn(col, df.columns)


if __name__ == "__main__":
    unittest.main()
