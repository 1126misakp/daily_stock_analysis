# -*- coding: utf-8 -*-
"""
P2-5 单测：营业部→游资近似映射（hot_money_seats）+ DataFetcherManager.get_hot_money_seats。

机制：基于公开知名游资席位（营业部名关键词）做子串匹配的近似"认人"，
免上 Tushare 10000 档付费游资识别。对 top_inst 席位明细做标注。
"""

import sys
import unittest
from unittest.mock import patch

import pandas as pd

from data_provider.hot_money_seats import (
    HOT_MONEY_SEATS,
    annotate_seats,
    classify_seat,
)


class TestClassifySeat(unittest.TestCase):
    def test_known_lasa_seat(self):
        name = "东方财富证券股份有限公司拉萨团结路第二证券营业部"
        self.assertIsNotNone(classify_seat(name))

    def test_known_shenzhen_yitian(self):
        name = "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部"
        self.assertIsNotNone(classify_seat(name))

    def test_unknown_seat_returns_none(self):
        self.assertIsNone(classify_seat("某不知名证券营业部"))

    def test_empty_or_none(self):
        self.assertIsNone(classify_seat(""))
        self.assertIsNone(classify_seat(None))

    def test_mapping_nonempty(self):
        self.assertTrue(len(HOT_MONEY_SEATS) >= 5)


class TestAnnotateSeats(unittest.TestCase):
    def test_adds_hot_money_field(self):
        records = [
            {"exalter": "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部", "net_buy": 1e8},
            {"exalter": "某不知名证券营业部", "net_buy": 2e7},
        ]
        out = annotate_seats(records)
        self.assertIsNotNone(out[0]["hot_money"])
        self.assertIsNone(out[1]["hot_money"])
        # 不破坏原字段
        self.assertEqual(out[0]["net_buy"], 1e8)

    def test_handles_missing_exalter(self):
        out = annotate_seats([{"net_buy": 1}])
        self.assertIsNone(out[0]["hot_money"])


_TOP_INST = pd.DataFrame(
    {
        "trade_date": ["20260529"] * 3,
        "ts_code": ["600000.SH", "600000.SH", "000001.SZ"],
        "exalter": [
            "华泰证券股份有限公司深圳益田路荣超商务中心证券营业部",
            "某不知名证券营业部",
            "另一只股票的席位",
        ],
        "buy": [1e8, 2e7, 3e7],
        "sell": [1e6, 5e6, 1e6],
        "net_buy": [9.9e7, 1.5e7, 2.9e7],
        "side": ["0", "0", "0"],
        "reason": ["涨幅偏离"] * 3,
    }
)


class _FakeTushare:
    name = "TushareFetcher"
    priority = -1

    def __init__(self, top_inst=_TOP_INST):
        self._top_inst = top_inst

    def is_available(self):
        return True

    def get_top_inst(self, trade_date):
        return self._top_inst


class _FakeTushareWithCalendar:
    """带交易日历的假 TushareFetcher，记录实际查询的 trade_date。"""

    name = "TushareFetcher"
    priority = -1

    def __init__(self, trade_dates, top_inst=_TOP_INST):
        self._trade_dates = trade_dates
        self._top_inst = top_inst
        self.queried_date = None

    def is_available(self):
        return True

    def _get_trade_dates(self, end_date=None):
        return list(self._trade_dates)

    def get_top_inst(self, trade_date):
        self.queried_date = trade_date
        return self._top_inst


def _manager(fetcher):
    from data_provider.base import DataFetcherManager

    return DataFetcherManager(fetchers=[fetcher])


class TestManagerHotMoneySeats(unittest.TestCase):
    def test_filters_by_ts_code_and_annotates(self):
        result = _manager(_FakeTushare()).get_hot_money_seats("600000", trade_date="20260529")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source"], "tushare")
        seats = result["seats"]
        # 仅保留 600000.SH 两条席位（000001 被过滤）
        self.assertEqual(len(seats), 2)
        tagged = [s for s in seats if s["hot_money"]]
        self.assertEqual(len(tagged), 1)
        self.assertEqual(result["hot_money_count"], 1)

    def test_non_cn_not_supported(self):
        result = _manager(_FakeTushare()).get_hot_money_seats("AAPL")
        self.assertEqual(result["status"], "not_supported")

    def test_no_tushare_failed(self):
        class _Dummy:
            name = "DummyFetcher"
            priority = 0

        result = _manager(_Dummy()).get_hot_money_seats("600000", trade_date="20260529")
        self.assertEqual(result["status"], "failed")

    def test_empty_top_inst_returns_status_empty(self):
        # W2：龙虎榜当日无数据 → status="empty"（与"确实有席位"的 ok 区分）
        result = _manager(_FakeTushare(top_inst=pd.DataFrame())).get_hot_money_seats(
            "600000", trade_date="20260529"
        )
        self.assertEqual(result["seats"], [])
        self.assertEqual(result["status"], "empty")

    def test_no_matching_stock_returns_status_empty(self):
        # W2：top_inst 非空但无本股席位 → 同样 empty
        result = _manager(_FakeTushare()).get_hot_money_seats(
            "999999", trade_date="20260529"
        )
        self.assertEqual(result["seats"], [])
        self.assertEqual(result["status"], "empty")

    def test_default_trade_date_uses_latest_trading_day(self):
        # W1：未显式指定 trade_date 时取交易日历最近交易日，而非本机 now()
        f = _FakeTushareWithCalendar(trade_dates=["20260529", "20260528"])
        result = _manager(f).get_hot_money_seats("600000")  # 不传 trade_date
        self.assertEqual(f.queried_date, "20260529")
        self.assertEqual(result["trade_date"], "20260529")

    def test_import_failure_fails_open(self):
        # 极端：hot_money_seats 模块不可导入时不应向上抛异常拖垮主流程，
        # 应 fail-open 返回 status="failed"
        with patch.dict(sys.modules, {"data_provider.hot_money_seats": None}):
            result = _manager(_FakeTushare()).get_hot_money_seats(
                "600000", trade_date="20260529"
            )
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
