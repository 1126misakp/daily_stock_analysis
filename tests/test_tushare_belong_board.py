# -*- coding: utf-8 -*-
"""
P2-1 单测：TushareFetcher.get_belong_board 反向板块/概念成分查询。

机制：
- ths_member(con_code=ts_code) 反查个股所属同花顺板块代码（无板块名）；
- join ths_index 取板块名 + 类型，仅保留 行业(I)/概念(N)/地域(R)，过滤宽基/风格/策略(BB/S/ST/TH)；
- index_member_all(ts_code) 补充申万一级/二级行业。
返回 [{name, code, type}]，按 name 去重；无数据返回 None；非 A 股或异常降级 None。
"""

import unittest

import pandas as pd

from data_provider.tushare_fetcher import TushareFetcher


class _FakeApi:
    """按 api_name 返回预置 df 的假 Tushare client。"""

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
    f._ths_index_map = None
    f._ths_index_map_date = None
    return f


_THS_MEMBER = pd.DataFrame(
    {
        "ts_code": ["700001.TI", "882001.TI", "885001.TI", "884001.TI", "999999.TI"],
        "con_code": ["600000.SH"] * 5,
        "con_name": ["浦发银行"] * 5,
    }
)
# ths_index：含各类型，验证仅 I/N/R 被保留
_THS_INDEX = pd.DataFrame(
    {
        "ts_code": ["700001.TI", "882001.TI", "885001.TI", "884001.TI"],
        "name": ["银行", "上海", "证金持股", "同花顺全A"],
        "type": ["I", "R", "N", "BB"],
    }
)
_SW = pd.DataFrame(
    {
        "l1_code": ["801780.SI"],
        "l1_name": ["银行"],  # 与 THS 行业同名，应被去重
        "l2_code": ["801783.SI"],
        "l2_name": ["股份制银行Ⅱ"],
        "ts_code": ["600000.SH"],
        "name": ["浦发银行"],
    }
)


class TestTushareBelongBoard(unittest.TestCase):
    def test_maps_ths_boards_and_filters_noise(self):
        api = _FakeApi(
            {"ths_member": _THS_MEMBER, "ths_index": _THS_INDEX, "index_member_all": _SW}
        )
        f = _fetcher(api)
        boards = f.get_belong_board("600000")
        self.assertIsInstance(boards, list)
        by_name = {b["name"]: b for b in boards}
        # 行业(I)/地域(R)/概念(N) 保留并映射成中文类型
        self.assertEqual(by_name["银行"]["type"], "行业")
        self.assertEqual(by_name["上海"]["type"], "地域")
        self.assertEqual(by_name["证金持股"]["type"], "概念")
        # 宽基指数(BB) 被过滤
        self.assertNotIn("同花顺全A", by_name)
        # 板块代码透传
        self.assertEqual(by_name["证金持股"]["code"], "885001.TI")

    def test_supplements_sw_industry(self):
        api = _FakeApi(
            {"ths_member": _THS_MEMBER, "ths_index": _THS_INDEX, "index_member_all": _SW}
        )
        f = _fetcher(api)
        boards = f.get_belong_board("600000")
        names = {b["name"] for b in boards}
        # 申万二级行业作为补充行业板块
        self.assertIn("股份制银行Ⅱ", names)

    def test_dedupes_by_name(self):
        api = _FakeApi(
            {"ths_member": _THS_MEMBER, "ths_index": _THS_INDEX, "index_member_all": _SW}
        )
        f = _fetcher(api)
        boards = f.get_belong_board("600000")
        names = [b["name"] for b in boards]
        # "银行" 同时来自 THS(I) 和 申万 l1，只应出现一次
        self.assertEqual(names.count("银行"), 1)

    def test_returns_none_when_no_data(self):
        api = _FakeApi({})  # 所有接口返回空
        f = _fetcher(api)
        self.assertIsNone(f.get_belong_board("600000"))

    def test_returns_none_on_exception(self):
        api = _FakeApi(raises=True)
        f = _fetcher(api)
        self.assertIsNone(f.get_belong_board("600000"))

    def test_returns_none_when_api_uninitialized(self):
        f = _fetcher(None)
        self.assertIsNone(f.get_belong_board("600000"))

    def test_uses_con_code_for_reverse_lookup(self):
        api = _FakeApi(
            {"ths_member": _THS_MEMBER, "ths_index": _THS_INDEX, "index_member_all": _SW}
        )
        f = _fetcher(api)
        f.get_belong_board("600000")
        ths_member_calls = [kw for name, kw in api.calls if name == "ths_member"]
        self.assertTrue(ths_member_calls)
        self.assertEqual(ths_member_calls[0].get("con_code"), "600000.SH")


class _BoardFetcher:
    """最小假 fetcher：仅声明 name/priority/get_belong_board，供 manager 路由测试。"""

    def __init__(self, name, priority, boards):
        self.name = name
        self.priority = priority
        self._boards = boards
        self.called = False

    def get_belong_board(self, stock_code):
        self.called = True
        return self._boards


class TestBelongBoardsRouting(unittest.TestCase):
    """机制①遍历链：Tushare(priority=-1) 应先于 efinance(0) 命中并被正确归一。"""

    def test_manager_prefers_tushare_and_normalizes(self):
        from data_provider.base import DataFetcherManager

        tushare = _BoardFetcher(
            "TushareFetcher",
            -1,
            [{"name": "银行", "code": "700001.TI", "type": "行业"}],
        )
        efinance = _BoardFetcher(
            "EfinanceFetcher",
            0,
            [{"name": "其它板块", "code": "X", "type": "概念"}],
        )
        mgr = DataFetcherManager(fetchers=[efinance, tushare])
        boards = mgr.get_belong_boards("600000")
        self.assertTrue(tushare.called)
        self.assertFalse(efinance.called, "Tushare 命中后不应再问 efinance")
        names = {b["name"] for b in boards}
        self.assertIn("银行", names)
        self.assertNotIn("其它板块", names)


if __name__ == "__main__":
    unittest.main()
