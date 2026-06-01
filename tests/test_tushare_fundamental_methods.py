# -*- coding: utf-8 -*-
"""
P1-1 单测：TushareFetcher 新增的基本面/资金流/龙虎榜接口薄封装方法。

每个方法：用 _convert_stock_code 生成 ts_code（个股类），经 _call_api_with_rate_limit
调用对应 Tushare 接口，空/异常返回 None（降级不断流程），正常返回原始 DataFrame。
"""

import unittest

import pandas as pd

from data_provider.tushare_fetcher import TushareFetcher


class _FakeApi:
    """记录调用并返回预置 df 的假 Tushare HTTP client。"""

    def __init__(self, returns=None, raises=False):
        self.calls = []
        self._returns = returns if returns is not None else pd.DataFrame({"ts_code": ["600000.SH"], "x": [1]})
        self._raises = raises

    def __getattr__(self, api_name):
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def caller(**kwargs):
            self.calls.append((api_name, kwargs))
            if self._raises:
                raise RuntimeError("boom")
            return self._returns

        return caller


def _fetcher(api):
    f = TushareFetcher.__new__(TushareFetcher)
    f.rate_limit_per_minute = 80
    f._call_count = 0
    f._minute_start = None
    f._api = api
    return f


class TestTushareFundamentalMethods(unittest.TestCase):
    def test_get_moneyflow_calls_moneyflow_with_ts_code(self):
        api = _FakeApi()
        f = _fetcher(api)
        df = f.get_moneyflow("600000", start_date="20260501", end_date="20260529")
        self.assertIsInstance(df, pd.DataFrame)
        name, kwargs = api.calls[0]
        self.assertEqual(name, "moneyflow")
        self.assertEqual(kwargs.get("ts_code"), "600000.SH")
        self.assertEqual(kwargs.get("start_date"), "20260501")
        self.assertEqual(kwargs.get("end_date"), "20260529")

    def test_get_top_list_calls_top_list_with_trade_date(self):
        api = _FakeApi()
        f = _fetcher(api)
        df = f.get_top_list("20260529")
        self.assertIsInstance(df, pd.DataFrame)
        name, kwargs = api.calls[0]
        self.assertEqual(name, "top_list")
        self.assertEqual(kwargs.get("trade_date"), "20260529")

    def test_get_top_inst_calls_top_inst_with_trade_date(self):
        api = _FakeApi()
        f = _fetcher(api)
        f.get_top_inst("20260529")
        name, kwargs = api.calls[0]
        self.assertEqual(name, "top_inst")
        self.assertEqual(kwargs.get("trade_date"), "20260529")

    def test_financial_statement_methods_use_ts_code(self):
        for method, api_name in (
            ("get_income_statement", "income"),
            ("get_cashflow_statement", "cashflow"),
            ("get_fina_indicator", "fina_indicator"),
            ("get_top10_holders", "top10_holders"),
        ):
            api = _FakeApi()
            f = _fetcher(api)
            getattr(f, method)("600000")
            name, kwargs = api.calls[0]
            self.assertEqual(name, api_name, f"{method} 应调用 {api_name}")
            self.assertEqual(kwargs.get("ts_code"), "600000.SH")

    def test_methods_return_none_on_empty(self):
        api = _FakeApi(returns=pd.DataFrame())
        f = _fetcher(api)
        self.assertIsNone(f.get_moneyflow("600000"))
        self.assertIsNone(f.get_fina_indicator("600000"))

    def test_methods_return_none_on_exception(self):
        api = _FakeApi(raises=True)
        f = _fetcher(api)
        self.assertIsNone(f.get_top_list("20260529"))
        self.assertIsNone(f.get_income_statement("600000"))

    def test_methods_return_none_when_api_uninitialized(self):
        f = _fetcher(None)
        self.assertIsNone(f.get_moneyflow("600000"))
        self.assertIsNone(f.get_top_list("20260529"))


if __name__ == "__main__":
    unittest.main()
