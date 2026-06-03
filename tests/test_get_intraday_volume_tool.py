# -*- coding: utf-8 -*-
"""get_intraday_volume agent 工具测试。

工具复用 intraday_volume 的 compute_slot_baselines + classify（同飞书告警口径），
单次 get_intraday_kline 取数，全程降级不抛错。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from datetime import datetime
from unittest.mock import MagicMock

import pandas as pd

from src.core.trading_calendar import MarketPhase
from src.agent.tools.analysis_tools import _handle_get_intraday_volume


_COLS = ["code", "datetime", "open", "high", "low", "close", "volume", "amount"]


def _cfg(**over):
    base = dict(
        intraday_volume_surge_ratio=2.0,
        intraday_volume_shrink_ratio=0.5,
        intraday_volume_baseline_days=20,
        intraday_volume_baseline_min_samples=2,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _intraday_df(
    code="600036",
    today="2026-06-03",
    hist_dates=("2026-05-29", "2026-05-30", "2026-06-02"),
    hist_vol=1000.0,
    today_closed_vol=3000.0,
    today_forming_vol=100.0,
    slot="10:05:00",
):
    """历史若干日同 slot bar + 今日已收 bar(slot) + 今日正在形成 bar(10:10)。"""
    rows = []
    for d in hist_dates:
        rows.append([code, f"{d} {slot}", 0, 0, 0, 38.0, hist_vol, 0])
    rows.append([code, f"{today} {slot}", 0, 0, 0, 38.5, today_closed_vol, 0])
    rows.append([code, f"{today} 10:10:00", 0, 0, 0, 38.6, today_forming_vol, 0])
    return pd.DataFrame(rows, columns=_COLS)


def _manager(df, *, name="招商银行", change_pct=1.23):
    m = MagicMock()
    m.get_intraday_kline.return_value = df
    m.get_stock_name.return_value = name
    m.get_realtime_quote.return_value = SimpleNamespace(price=38.5, change_pct=change_pct)
    return m


def _call(df, *, phase=MarketPhase.INTRADAY, now=datetime(2026, 6, 3, 10, 6, 0),
          cfg=None, manager=None):
    cfg = cfg or _cfg()
    manager = manager or _manager(df)
    return _handle_get_intraday_volume(
        "600036", _manager=manager, _config=cfg, _now=now, _phase=phase
    )


class IntradayVolumeToolTestCase(unittest.TestCase):
    def test_surge_intraday(self):
        r = _call(_intraday_df(today_closed_vol=3000.0))  # 3000/1000 = 3.0 >= 2.0
        self.assertNotIn("error", r)
        self.assertEqual(r["market_phase"], "intraday")
        self.assertEqual(r["latest_bar"]["verdict"], "surge")
        self.assertEqual(r["latest_bar"]["verdict_cn"], "放量")
        self.assertAlmostEqual(r["latest_bar"]["ratio"], 3.0, places=2)
        self.assertEqual(r["stock_name"], "招商银行")
        self.assertIn("盘中", r["as_of_note"])

    def test_shrink_intraday(self):
        r = _call(_intraday_df(today_closed_vol=300.0))  # 300/1000 = 0.3 <= 0.5
        self.assertEqual(r["latest_bar"]["verdict"], "shrink")
        self.assertEqual(r["latest_bar"]["verdict_cn"], "缩量")

    def test_normal_intraday(self):
        r = _call(_intraday_df(today_closed_vol=1200.0))  # 1.2 between thresholds
        self.assertEqual(r["latest_bar"]["verdict"], "normal")
        self.assertEqual(r["latest_bar"]["verdict_cn"], "正常")

    def test_today_cumulative_volume(self):
        r = _call(_intraday_df(today_closed_vol=3000.0, today_forming_vol=100.0))
        self.assertAlmostEqual(r["today_cumulative_volume"], 3100.0, places=2)

    def test_baseline_missing_no_error(self):
        # 仅 1 个历史样本 < min_samples=2 → 无基线 → normal + note，不报错
        r = _call(_intraday_df(hist_dates=("2026-06-02",)))
        self.assertNotIn("error", r)
        self.assertEqual(r["latest_bar"]["verdict"], "normal")
        self.assertIsNone(r["latest_bar"]["ratio"])
        self.assertTrue(r.get("note"))

    def test_non_a_share_or_no_data_returns_error(self):
        m = _manager(None)
        r = _call(None, manager=m)
        self.assertIn("error", r)

    def test_fetch_exception_degrades_no_raise(self):
        m = MagicMock()
        m.get_intraday_kline.side_effect = RuntimeError("net down")
        r = _call(None, manager=m)  # 不抛异常
        self.assertIn("error", r)

    def test_change_pct_best_effort_null_on_failure(self):
        m = _manager(_intraday_df(today_closed_vol=3000.0))
        m.get_realtime_quote.side_effect = RuntimeError("quote fail")
        r = _call(None, manager=m)
        self.assertEqual(r["latest_bar"]["verdict"], "surge")  # 量能判定不受影响
        self.assertIsNone(r["change_pct"])

    def test_non_trading_day_uses_last_bar_and_notes_recent_session(self):
        # 周六调用，df 最后一根是最近交易日 2026-06-05 的已收 bar
        df = _intraday_df(
            today="2026-06-05",
            hist_dates=("2026-06-02", "2026-06-03", "2026-06-04"),
        )
        r = _call(
            df,
            phase=MarketPhase.NON_TRADING,
            now=datetime(2026, 6, 6, 10, 0, 0),
        )
        self.assertNotIn("error", r)
        self.assertEqual(r["market_phase"], "non_trading")
        self.assertIn("2026-06-05", r["as_of_note"])

    def test_tool_registered_and_in_technical_whitelist(self):
        from src.agent.agents.technical_agent import TechnicalAgent
        from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS

        names = [t.name for t in ALL_ANALYSIS_TOOLS]
        self.assertIn("get_intraday_volume", names)
        self.assertIn("get_intraday_volume", TechnicalAgent.tool_names)

    def test_price_is_rounded(self):
        df = _intraday_df(today_closed_vol=3000.0)
        df.loc[df.index[-2], "close"] = 38.499999999999986  # 浮点噪声（参考 bar=倒数第二根）
        r = _call(df)
        self.assertEqual(r["price"], 38.5)

    def test_recent_bars_shape(self):
        r = _call(_intraday_df(today_closed_vol=3000.0))
        self.assertIsInstance(r["recent_bars"], list)
        self.assertGreaterEqual(len(r["recent_bars"]), 1)
        for b in r["recent_bars"]:
            self.assertIn("time", b)
            self.assertIn("volume", b)
            self.assertIn("ratio", b)


if __name__ == "__main__":
    unittest.main()
