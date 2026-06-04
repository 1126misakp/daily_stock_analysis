# -*- coding: utf-8 -*-
"""
P1-2/P1-3 单测：AkshareFundamentalAdapter 在注入 tushare_provider 后，
对资金流/龙虎榜/财务区块优先取 Tushare，失败回退 akshare；返回结构/键名不变。

所有 akshare 路径用 patch _call_df_candidates 隔离，保证离线确定性。
"""

import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from data_provider.fundamental_adapter import AkshareFundamentalAdapter


class _FakeTushare:
    """最小假 TushareFetcher，仅实现 P1-1 方法。"""

    def __init__(self, trade_dates=None, **frames):
        self._frames = frames
        self._trade_dates = trade_dates or []
        self.calls = []

    def _get_trade_dates(self, end_date=None):
        return list(self._trade_dates)

    def _ret(self, key, *a, **k):
        self.calls.append((key, a, k))
        return self._frames.get(key)

    def get_moneyflow(self, stock_code, start_date=None, end_date=None):
        return self._ret("moneyflow", stock_code, start_date, end_date)

    def get_top_list(self, trade_date):
        return self._ret(f"top_list:{trade_date}", trade_date)

    def get_top_inst(self, trade_date):
        return self._ret(f"top_inst:{trade_date}", trade_date)

    def get_fina_indicator(self, stock_code):
        return self._ret("fina_indicator", stock_code)

    def get_income_statement(self, stock_code):
        return self._ret("income", stock_code)

    def get_cashflow_statement(self, stock_code):
        return self._ret("cashflow", stock_code)

    def get_top10_holders(self, stock_code):
        return self._ret("top10_holders", stock_code)


def _adapter(tushare=None):
    return AkshareFundamentalAdapter(tushare_provider=(lambda: tushare))


# ---------------- 资金流 (P1-3) ----------------
class TestCapitalFlowTushareFirst(unittest.TestCase):
    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_stock_flow_prefers_tushare_moneyflow(self, _mock_ak):
        mf = pd.DataFrame({
            "ts_code": ["600000.SH"] * 3,
            "trade_date": ["20260527", "20260528", "20260529"],
            "net_mf_amount": [100.0, 200.0, 300.0],  # 万元
        })
        adapter = _adapter(_FakeTushare(moneyflow=mf))

        res = adapter.get_capital_flow("600000")

        # 万元 → 元（×1e4），与 akshare 口径对齐
        self.assertEqual(res["stock_flow"]["main_net_inflow"], 300.0 * 1e4)
        self.assertEqual(res["stock_flow"]["inflow_5d"], 600.0 * 1e4)
        self.assertEqual(res["stock_flow"]["inflow_10d"], 600.0 * 1e4)
        self.assertIn("capital_stock:tushare:moneyflow", res["source_chain"])
        # 板块资金流排名已移除（改用 get_sector_rankings），不再返回 sector_rankings 键
        self.assertEqual(set(res.keys()), {"status", "stock_flow", "source_chain", "errors"})

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_falls_back_to_akshare_when_no_tushare(self, _mock_ak):
        adapter = _adapter(None)
        res = adapter.get_capital_flow("600000")
        self.assertNotIn("capital_stock:tushare:moneyflow", res["source_chain"])

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_falls_back_when_tushare_moneyflow_empty(self, _mock_ak):
        adapter = _adapter(_FakeTushare(moneyflow=None))
        res = adapter.get_capital_flow("600000")
        self.assertNotIn("capital_stock:tushare:moneyflow", res["source_chain"])

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_falls_back_when_moneyflow_missing_trade_date(self, _mock_ak):
        # net_mf_amount 在但缺 trade_date 列：排序应 fail-open（返回 None 回退 akshare），
        # 不应抛 KeyError 拖垮 get_capital_flow 绕过兜底
        mf = pd.DataFrame({"ts_code": ["600000.SH"], "net_mf_amount": [100.0]})
        adapter = _adapter(_FakeTushare(moneyflow=mf))
        res = adapter.get_capital_flow("600000")  # 不应抛异常
        self.assertNotIn("capital_stock:tushare:moneyflow", res["source_chain"])


# ---------------- 龙虎榜 (P1-3) ----------------
@patch("data_provider.fundamental_adapter.datetime")
class TestDragonTigerTushareFirst(unittest.TestCase):
    def _fixed_now(self, mock_dt):
        mock_dt.now.return_value = datetime(2026, 5, 31)

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_dragon_tiger_prefers_tushare_top_list(self, _mock_ak, mock_dt):
        self._fixed_now(mock_dt)
        hit = pd.DataFrame({"ts_code": ["600000.SH", "000001.SZ"], "name": ["浦发银行", "平安银行"]})
        miss = pd.DataFrame({"ts_code": ["000002.SZ"], "name": ["万科A"]})
        tushare = _FakeTushare(
            trade_dates=["20260529", "20260528"],
            **{"top_list:20260529": hit, "top_list:20260528": miss},
        )
        adapter = _adapter(tushare)

        res = adapter.get_dragon_tiger_flag("600000", lookback_days=20)

        self.assertTrue(res["is_on_list"])
        self.assertEqual(res["recent_count"], 1)
        self.assertEqual(res["latest_date"], "2026-05-29")
        self.assertEqual(res["status"], "ok")
        self.assertIn("dragon_tiger:tushare:top_list", res["source_chain"])
        self.assertEqual(
            set(res.keys()),
            {"status", "is_on_list", "recent_count", "latest_date", "source_chain", "errors"},
        )

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_dragon_tiger_not_on_list(self, _mock_ak, mock_dt):
        self._fixed_now(mock_dt)
        miss = pd.DataFrame({"ts_code": ["000002.SZ"], "name": ["万科A"]})
        tushare = _FakeTushare(
            trade_dates=["20260529"],
            **{"top_list:20260529": miss},
        )
        res = _adapter(tushare).get_dragon_tiger_flag("600000")
        self.assertFalse(res["is_on_list"])
        self.assertEqual(res["recent_count"], 0)
        self.assertIsNone(res["latest_date"])
        self.assertIn("dragon_tiger:tushare:top_list", res["source_chain"])

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_dragon_tiger_falls_back_when_no_tushare_data(self, _mock_ak, mock_dt):
        self._fixed_now(mock_dt)
        # 无交易日 / 全部取数失败 → 回退 akshare（无 tushare source）
        tushare = _FakeTushare(trade_dates=["20260529"])  # 无对应 top_list 帧 → None
        res = _adapter(tushare).get_dragon_tiger_flag("600000")
        self.assertNotIn("dragon_tiger:tushare:top_list", res["source_chain"])


# ---------------- 财务 (P1-2) ----------------
class TestFundamentalBundleTushareFirst(unittest.TestCase):
    def _frames(self):
        fina = pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20251231", "20260331"],  # 乱序，应取最新 20260331
            "or_yoy": [9.9, 1.42],
            "netprofit_yoy": [8.8, 1.49],
            "roe": [8.65, 2.16],
            "grossprofit_margin": [None, 30.0],
        })
        income = pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH"],
            "end_date": ["20251231", "20260331"],
            "report_type": ["1", "1"],
            "revenue": [173964000000.0, 46573000000.0],
            "total_revenue": [173964000000.0, 46573000000.0],
            "n_income_attr_p": [50017000000.0, 17861000000.0],
        })
        cashflow = pd.DataFrame({
            "ts_code": ["600000.SH"],
            "end_date": ["20260331"],
            "report_type": ["1"],
            "n_cashflow_act": [91724000000.0],
        })
        top10 = pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH", "600000.SH"],
            "end_date": ["20251231", "20260331", "20260331"],  # 旧期应排除
            "holder_name": ["旧股东", "上海国际集团", "某基金"],
            "hold_change": [99.0, 0.0, 5.0],
        })
        return dict(fina_indicator=fina, income=income, cashflow=cashflow, top10_holders=top10)

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_growth_and_report_and_top10_prefer_tushare(self, _mock_ak):
        adapter = _adapter(_FakeTushare(**self._frames()))
        res = adapter.get_fundamental_bundle("600000")

        # 取最新报告期 20260331
        self.assertEqual(res["growth"]["revenue_yoy"], 1.42)
        self.assertEqual(res["growth"]["net_profit_yoy"], 1.49)
        self.assertEqual(res["growth"]["roe"], 2.16)
        self.assertEqual(res["growth"]["gross_margin"], 30.0)

        report = res["earnings"]["financial_report"]
        self.assertEqual(report["report_date"], "2026-03-31")
        self.assertEqual(report["revenue"], 46573000000.0)
        self.assertEqual(report["net_profit_parent"], 17861000000.0)
        self.assertEqual(report["operating_cash_flow"], 91724000000.0)
        self.assertEqual(report["roe"], 2.16)

        # 最新期 top10 hold_change 求和 0+5=5，旧期 99 排除
        self.assertEqual(res["institution"]["top10_holder_change"], 5.0)

        for tag in ("growth:tushare:fina_indicator", "financial_report:tushare:income", "top10:tushare:top10_holders"):
            self.assertIn(tag, res["source_chain"])

        self.assertEqual(res["status"], "partial")
        self.assertEqual(
            set(res.keys()),
            {"status", "growth", "earnings", "institution", "source_chain", "errors"},
        )

    @patch.object(AkshareFundamentalAdapter, "_call_df_candidates", return_value=(None, None, []))
    def test_falls_back_to_akshare_without_tushare(self, _mock_ak):
        res = _adapter(None).get_fundamental_bundle("600000")
        for tag in ("growth:tushare:fina_indicator", "financial_report:tushare:income"):
            self.assertNotIn(tag, res["source_chain"])


if __name__ == "__main__":
    unittest.main()
