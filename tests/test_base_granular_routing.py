# -*- coding: utf-8 -*-
"""DataFetcherManager granular 薄路由方法测试：Tushare 优先、akshare 末位、hasattr 守卫。"""
import unittest
from unittest.mock import MagicMock

import pandas as pd

from data_provider.base import DataFetcherManager


class _FetcherTushare:
    name = "tushare"
    def get_income_statement(self, stock_code):
        return pd.DataFrame([{"end_date": "20251231", "revenue": 100}])


class _FetcherAkshareOnlyName:
    """模拟没有该方法的 fetcher（hasattr 应跳过，不报错）。"""
    name = "akshare"


def _manager_with(fetchers):
    mgr = DataFetcherManager.__new__(DataFetcherManager)  # 跳过 __init__ 真实初始化
    mgr._fetchers = fetchers
    return mgr


class TestGranularRouting(unittest.TestCase):
    def test_income_statement_uses_first_fetcher_with_method(self):
        mgr = _manager_with([_FetcherAkshareOnlyName(), _FetcherTushare()])
        df = mgr.get_income_statement("600519")
        self.assertIsNotNone(df)
        self.assertEqual(df.iloc[0]["revenue"], 100)

    def test_returns_none_when_no_fetcher_has_data(self):
        empty = MagicMock()
        empty.name = "x"
        empty.get_pledge_detail.return_value = None
        mgr = _manager_with([empty])
        self.assertIsNone(mgr.get_pledge_detail("600519"))

    def test_repurchase_passes_date_args(self):
        f = MagicMock()
        f.name = "tushare"
        f.get_repurchase.return_value = pd.DataFrame([{"ann_date": "20250101"}])
        mgr = _manager_with([f])
        out = mgr.get_repurchase("600519", start_date="20250101", end_date="20250201")
        self.assertIsNotNone(out)
        f.get_repurchase.assert_called_once_with("600519", start_date="20250101", end_date="20250201")

    def test_non_a_share_returns_none_without_calling_fetcher(self):
        """granular 能力 A 股专属：港股/美股代码应早返回 None，不触达 fetcher。"""
        f = MagicMock()
        f.name = "tushare"
        mgr = _manager_with([f])
        self.assertIsNone(mgr.get_income_statement("AAPL"))
        self.assertIsNone(mgr.get_income_statement("hk00700"))
        f.get_income_statement.assert_not_called()


if __name__ == "__main__":
    unittest.main()
