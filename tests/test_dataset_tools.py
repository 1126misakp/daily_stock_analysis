# -*- coding: utf-8 -*-
"""dataset_tools：新数据工具 handler 经 manager 取数、DataFrame 序列化、None 容错。"""
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

import src.agent.tools.dataset_tools as dt


class TestDatasetTools(unittest.TestCase):
    def setUp(self):
        self.mgr = MagicMock()
        patcher = patch.object(dt, "_get_fetcher_manager", return_value=self.mgr)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_all_dataset_tools_exact_16(self):
        names = {t.name for t in dt.ALL_DATASET_TOOLS}
        expected = {
            "get_income_statement", "get_cashflow_statement", "get_financial_indicators",
            "get_pledge_detail", "get_holder_trade", "get_share_float", "get_repurchase",
            "get_dragon_tiger", "get_risk_assessment", "get_stock_sectors",
            "get_intraday_kline", "get_order_book", "get_limit_up_pool",
            "get_hot_stocks", "get_concept_rankings", "get_market_stats",
        }
        self.assertEqual(len(dt.ALL_DATASET_TOOLS), 16)
        self.assertEqual(names, expected)  # 恰好这 16 个，锁死回归
        self.assertNotIn("get_price_percentile", names)  # 已被 get_risk_assessment 替换

    def test_income_statement_serializes_dataframe(self):
        self.mgr.get_income_statement.return_value = pd.DataFrame(
            [{"end_date": "20251231", "revenue": 100}])
        out = dt._handle_single_code_df("get_income_statement", "600519")
        self.assertEqual(out["stock_code"], "600519")
        self.assertEqual(out["items"][0]["revenue"], 100)
        self.mgr.get_income_statement.assert_called_once_with("600519")

    def test_none_returns_info(self):
        self.mgr.get_pledge_detail.return_value = None
        out = dt._handle_single_code_df("get_pledge_detail", "600519")
        self.assertIn("info", out)

    def test_dragon_tiger_returns_dict_passthrough(self):
        self.mgr.get_dragon_tiger_context.return_value = {"has_data": True}
        out = dt._handle_dragon_tiger("600519")
        self.assertEqual(out, {"has_data": True})

    def test_market_stats_no_arg(self):
        self.mgr.get_market_stats.return_value = {"up_count": 3000}
        out = dt._handle_market_stats()
        self.assertEqual(out["up_count"], 3000)

    def test_concept_rankings_tuple(self):
        self.mgr.get_concept_rankings.return_value = ([{"name": "AI"}], [{"name": "煤炭"}])
        out = dt._handle_concept_rankings(5)
        self.assertEqual(out["top"][0]["name"], "AI")
        self.assertEqual(out["bottom"][0]["name"], "煤炭")


if __name__ == "__main__":
    unittest.main()
