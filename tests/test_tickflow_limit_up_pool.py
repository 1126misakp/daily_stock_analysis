# -*- coding: utf-8 -*-
"""
P2-4 单测：TickFlowFetcher.get_limit_up_pool 自建涨停池。

机制：universe(CN_Equity_A) 行情 + 计算涨停价（复用 _get_limit_ratio/_round_limit_price，
与 get_market_stats 的涨停判定同口径），last_price 触及涨停价入池，按成交额降序、截断 n。
进链 priority=-2，自然优先于 akshare 兜底（base.py get_limit_up_pool 遍历链）。
无标的池权限/空 → None（回退后续源）。
"""

import unittest
from datetime import datetime

from data_provider.tickflow_fetcher import TickFlowFetcher


def _quote(symbol, last_price, prev_close, amount, name="股票", change_pct=0.10):
    return {
        "symbol": symbol,
        "last_price": last_price,
        "prev_close": prev_close,
        "amount": amount,
        "ext": {"name": name, "change_pct": change_pct},
    }


class _FakeQuotes:
    def __init__(self, universe_data):
        self._universe_data = universe_data
        self.calls = []

    def get(self, *, symbols=None, universes=None, as_dataframe=False):
        self.calls.append({"symbols": symbols, "universes": universes})
        if universes is not None:
            if isinstance(self._universe_data, Exception):
                raise self._universe_data
            return self._universe_data
        return []


class _FakeClient:
    def __init__(self, universe_data):
        self.quotes = _FakeQuotes(universe_data)

    def close(self):
        return None


class _PermissionError(Exception):
    def __init__(self):
        super().__init__("标的池查询无权限")
        self.status_code = 403
        self.code = "FORBIDDEN"


def _fetcher(universe_data):
    f = TickFlowFetcher(api_key="dummy-key")
    f._client = _FakeClient(universe_data)
    return f


# 600000: prev 10.00 → 涨停价 11.00，last 11.00 命中（amount 5e8）
# 000001: prev 10.00 → last 10.50 未涨停
# 600519: prev 100.00 → 涨停价 110.00，last 110.00 命中（amount 9e8，应排第一）
_UNIVERSE = [
    _quote("600000.SH", 11.00, 10.00, 5e8, name="浦发银行"),
    _quote("000001.SZ", 10.50, 10.00, 3e8, name="平安银行"),
    _quote("600519.SH", 110.00, 100.00, 9e8, name="贵州茅台"),
    _quote("AAPL", 200.0, 180.0, 1e9, name="苹果"),  # 非 A 股，应被过滤
]


class TestLimitUpPool(unittest.TestCase):
    def test_pool_contains_only_limit_up_sorted_by_amount(self):
        pool = _fetcher(_UNIVERSE).get_limit_up_pool()
        codes = [p["code"] for p in pool]
        self.assertEqual(codes, ["600519", "600000"])  # 按成交额降序
        self.assertNotIn("000001", codes)  # 未涨停
        self.assertNotIn("AAPL", codes)  # 非 A 股

    def test_pool_entry_fields(self):
        pool = _fetcher(_UNIVERSE).get_limit_up_pool()
        top = pool[0]
        self.assertEqual(top["code"], "600519")
        self.assertEqual(top["name"], "贵州茅台")
        self.assertAlmostEqual(top["price"], 110.00)
        self.assertAlmostEqual(top["limit_up"], 110.00)
        self.assertAlmostEqual(top["amount"], 9e8)

    def test_pool_respects_n(self):
        pool = _fetcher(_UNIVERSE).get_limit_up_pool(n=1)
        self.assertEqual(len(pool), 1)
        self.assertEqual(pool[0]["code"], "600519")

    def test_queries_cn_equity_universe(self):
        f = _fetcher(_UNIVERSE)
        f.get_limit_up_pool()
        self.assertEqual(f._client.quotes.calls[0]["universes"], ["CN_Equity_A"])

    def test_returns_none_on_empty_universe(self):
        self.assertIsNone(_fetcher([]).get_limit_up_pool())

    def test_returns_none_on_permission_error(self):
        self.assertIsNone(_fetcher(_PermissionError()).get_limit_up_pool())

    def test_returns_none_when_no_limit_up(self):
        no_limit = [_quote("000001.SZ", 10.50, 10.00, 3e8)]
        self.assertIsNone(_fetcher(no_limit).get_limit_up_pool())

    def test_returns_none_for_non_current_date(self):
        # universe 是实时盘口快照，仅支持当日；历史日期应返回 None，
        # 让 base 遍历链回退到可查历史的后续数据源，避免日期错配。
        f = _fetcher(_UNIVERSE)
        self.assertIsNone(f.get_limit_up_pool(date="20200101"))
        # 不应触碰 universe 查询（提前短路返回）
        self.assertEqual(f._client.quotes.calls, [])

    def test_accepts_current_date_compact(self):
        today = datetime.now().strftime("%Y%m%d")
        pool = _fetcher(_UNIVERSE).get_limit_up_pool(date=today)
        self.assertEqual([p["code"] for p in pool], ["600519", "600000"])

    def test_accepts_current_date_dashed(self):
        today = datetime.now().strftime("%Y-%m-%d")
        pool = _fetcher(_UNIVERSE).get_limit_up_pool(date=today)
        self.assertIsNotNone(pool)
        self.assertEqual([p["code"] for p in pool], ["600519", "600000"])


if __name__ == "__main__":
    unittest.main()
