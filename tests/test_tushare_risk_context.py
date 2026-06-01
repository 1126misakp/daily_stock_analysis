# -*- coding: utf-8 -*-
"""
P2-3 单测：
1) TushareFetcher 新增风险/估值接口薄封装（daily_basic/stk_limit/share_float/
   pledge_detail/stk_holdertrade/margin_detail/block_trade/repurchase）。
2) DataFetcherManager.get_risk_context 聚合上述维度（fail-open，仅 A 股，源=tushare）。
"""

import unittest

import pandas as pd

from data_provider.tushare_fetcher import TushareFetcher


class _FakeApi:
    def __init__(self, returns_by_name=None, raises=False):
        self.calls = []
        self._returns_by_name = returns_by_name or {}
        self._raises = raises

    def __getattr__(self, api_name):
        if api_name.startswith("_"):
            raise AttributeError(api_name)

        def caller(**kwargs):
            self.calls.append((api_name, kwargs))
            if self._raises:
                raise RuntimeError("boom")
            return self._returns_by_name.get(api_name, pd.DataFrame())

        return caller


def _fetcher(api):
    f = TushareFetcher.__new__(TushareFetcher)
    f.rate_limit_per_minute = 80
    f._call_count = 0
    f._minute_start = None
    f._api = api
    return f


class TestTushareRiskMethods(unittest.TestCase):
    def test_methods_pass_ts_code_and_api_name(self):
        api = _FakeApi({k: pd.DataFrame({"ts_code": ["600000.SH"]}) for k in (
            "daily_basic", "stk_limit", "share_float", "pledge_detail",
            "stk_holdertrade", "margin_detail", "block_trade", "repurchase",
        )})
        f = _fetcher(api)
        f.get_daily_basic("600000", start_date="20260520", end_date="20260529")
        f.get_stk_limit("600000", start_date="20260520", end_date="20260529")
        f.get_share_float("600000")
        f.get_pledge_detail("600000")
        f.get_holder_trade("600000")
        f.get_margin_detail("600000", start_date="20260520", end_date="20260529")
        f.get_block_trade("600000", start_date="20260101", end_date="20260529")
        f.get_repurchase("600000", start_date="20260101", end_date="20260529")

        called = {name: kw for name, kw in api.calls}
        self.assertEqual(called["daily_basic"].get("ts_code"), "600000.SH")
        self.assertEqual(called["stk_limit"].get("start_date"), "20260520")
        self.assertEqual(called["share_float"].get("ts_code"), "600000.SH")
        self.assertEqual(called["stk_holdertrade"].get("ts_code"), "600000.SH")
        self.assertEqual(called["repurchase"].get("ts_code"), "600000.SH")

    def test_methods_return_none_on_empty_or_error(self):
        f = _fetcher(_FakeApi({}))  # 全空
        self.assertIsNone(f.get_daily_basic("600000"))
        self.assertIsNone(f.get_share_float("600000"))
        f2 = _fetcher(_FakeApi(raises=True))
        self.assertIsNone(f2.get_margin_detail("600000"))
        self.assertIsNone(f2.get_block_trade("600000"))

    def test_methods_return_none_when_api_uninitialized(self):
        f = _fetcher(None)
        self.assertIsNone(f.get_daily_basic("600000"))
        self.assertIsNone(f.get_stk_limit("600000"))


# ---- 聚合 get_risk_context ----

_DAILY_BASIC = pd.DataFrame(
    {
        "ts_code": ["600000.SH", "600000.SH"],
        "trade_date": ["20260528", "20260529"],
        "close": [9.36, 9.37],
        "turnover_rate": [0.27, 0.28],
        "volume_ratio": [0.9, 0.84],
        "pe_ttm": [6.18, 6.21],
        "pb": [0.41, 0.414],
        "ps_ttm": [1.78, 1.79],
        "dv_ratio": [3.97, 3.98],
        "total_mv": [31100000.0, 31207570.0],
        "circ_mv": [31100000.0, 31207570.0],
    }
)
_STK_LIMIT = pd.DataFrame(
    {"trade_date": ["20260528", "20260529"], "ts_code": ["600000.SH"] * 2,
     "up_limit": [10.12, 10.13], "down_limit": [8.28, 8.29]}
)
_MARGIN = pd.DataFrame(
    {"trade_date": ["20260528", "20260529"], "ts_code": ["600000.SH"] * 2,
     "rzye": [3.7e9, 3.8e9], "rqye": [9.0e6, 9.0e6], "rzrqye": [3.71e9, 3.81e9]}
)
_SHARE_FLOAT = pd.DataFrame(
    {"ts_code": ["600000.SH", "600000.SH"],
     "ann_date": ["20170906", "20990101"],
     "float_date": ["20200904", "20990601"],  # 一个过去、一个未来
     "float_share": [8.4e8, 1.0e8], "float_ratio": [2.86, 0.5],
     "holder_name": ["国际集团", "未来股东"], "share_type": ["定增", "定增"]}
)
_HOLDER_TRADE = pd.DataFrame(
    {"ts_code": ["600000.SH"], "ann_date": ["20250305"], "holder_name": ["国资经营"],
     "holder_type": ["C"], "in_de": ["IN"], "change_vol": [8.6e7],
     "change_ratio": [0.29], "after_ratio": [0.32], "avg_price": [None]}
)
_PLEDGE = pd.DataFrame(
    {"ts_code": ["600000.SH"], "ann_date": ["20140324"], "holder_name": ["雅戈尔"],
     "pledge_amount": [1200.0], "p_total_ratio": [0.06]}
)
_BLOCK = pd.DataFrame(
    {"ts_code": ["600000.SH"], "trade_date": ["20260116"], "price": [11.03],
     "vol": [22520.29], "amount": [248398.82], "buyer": ["券商A"], "seller": ["券商B"]}
)
_REPURCHASE = pd.DataFrame(
    {"ts_code": ["600000.SH"], "ann_date": ["20260201"], "proc": ["实施"],
     "vol": [6.5e6], "amount": [4.9e7]}
)


class _FakeTushare:
    name = "TushareFetcher"
    priority = -1

    def __init__(self, returns):
        self._r = returns

    def is_available(self):
        return True

    def get_daily_basic(self, code, **kw):
        return self._r.get("daily_basic")

    def get_stk_limit(self, code, **kw):
        return self._r.get("stk_limit")

    def get_share_float(self, code):
        return self._r.get("share_float")

    def get_pledge_detail(self, code):
        return self._r.get("pledge_detail")

    def get_holder_trade(self, code):
        return self._r.get("stk_holdertrade")

    def get_margin_detail(self, code, **kw):
        return self._r.get("margin_detail")

    def get_block_trade(self, code, **kw):
        return self._r.get("block_trade")

    def get_repurchase(self, code, **kw):
        return self._r.get("repurchase")


def _manager_with(returns):
    from data_provider.base import DataFetcherManager

    return DataFetcherManager(fetchers=[_FakeTushare(returns)])


class TestRiskContextAggregation(unittest.TestCase):
    def _full_returns(self):
        return {
            "daily_basic": _DAILY_BASIC, "stk_limit": _STK_LIMIT,
            "margin_detail": _MARGIN, "share_float": _SHARE_FLOAT,
            "stk_holdertrade": _HOLDER_TRADE, "pledge_detail": _PLEDGE,
            "block_trade": _BLOCK, "repurchase": _REPURCHASE,
        }

    def test_status_ok_and_source_tushare(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("600000")
        self.assertEqual(ctx["status"], "ok")
        self.assertEqual(ctx["source"], "tushare")

    def test_valuation_uses_latest_row(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("600000")
        val = ctx["valuation"]
        self.assertEqual(val["trade_date"], "20260529")
        self.assertAlmostEqual(val["pe_ttm"], 6.21)
        self.assertAlmostEqual(val["pb"], 0.414)

    def test_limit_and_margin_latest(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("600000")
        self.assertAlmostEqual(ctx["limit"]["up_limit"], 10.13)
        self.assertAlmostEqual(ctx["margin"]["rzrqye"], 3.81e9)

    def test_upcoming_float_only_future(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("600000")
        upcoming = ctx["upcoming_float"]
        # 仅保留未来解禁（float_date >= 今天），过去的 20200904 应被剔除
        names = {r["holder_name"] for r in upcoming}
        self.assertIn("未来股东", names)
        self.assertNotIn("国际集团", names)

    def test_recent_sections_present(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("600000")
        self.assertTrue(ctx["holder_trade"])
        self.assertTrue(ctx["pledge"]["records"])
        self.assertTrue(ctx["block_trade"])
        self.assertTrue(ctx["repurchase"])

    def test_non_cn_not_supported(self):
        ctx = _manager_with(self._full_returns()).get_risk_context("AAPL")
        self.assertEqual(ctx["status"], "not_supported")

    def test_no_tushare_failed(self):
        class _Dummy:
            name = "DummyFetcher"
            priority = 0

        from data_provider.base import DataFetcherManager

        ctx = DataFetcherManager(fetchers=[_Dummy()]).get_risk_context("600000")
        self.assertEqual(ctx["status"], "failed")

    def test_partial_when_some_sections_empty(self):
        returns = {"daily_basic": _DAILY_BASIC}  # 仅估值有数据
        ctx = _manager_with(returns).get_risk_context("600000")
        self.assertIn(ctx["status"], ("ok", "partial"))
        self.assertIsNotNone(ctx["valuation"])
        self.assertIsNone(ctx["limit"])


if __name__ == "__main__":
    unittest.main()
