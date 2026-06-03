# -*- coding: utf-8 -*-
"""盘中量能监控编排测试。"""
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from src.core.trading_calendar import MarketPhase
from src.services.intraday_volume_monitor import IntradayVolumeMonitor


def _cfg(**over):
    base = dict(
        stock_list=["600036"],
        intraday_volume_surge_ratio=2.0,
        intraday_volume_shrink_ratio=0.5,
        intraday_volume_baseline_days=20,
        intraday_volume_baseline_min_samples=2,
        intraday_volume_include_holdings=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _live_df(today, current_vol):
    # 倒数第二根为已收 bar（今天 10:05），最后一根为正在形成的 10:10
    rows = [
        ["600036", f"{today} 10:05:00", 0, 0, 0, 38.5, current_vol, 0],
        ["600036", f"{today} 10:10:00", 0, 0, 0, 38.6, 1, 0],
    ]
    return pd.DataFrame(rows, columns=["code", "datetime", "open", "high", "low", "close", "volume", "amount"])


class MonitorTestCase(unittest.TestCase):
    def _make(self, *, phase, df, baseline_value, cfg=None):
        cfg = cfg or _cfg()
        manager = MagicMock()
        manager.get_intraday_kline.return_value = df
        notifier = MagicMock()
        notifier.send.return_value = True
        now = datetime(2026, 6, 3, 10, 6, 0)
        monitor = IntradayVolumeMonitor(
            config_provider=lambda: cfg,
            manager=manager,
            notifier=notifier,
            phase_fn=lambda _now: phase,
            now_fn=lambda: now,
        )
        # 直接桩掉基线，隔离 detector/编排逻辑
        monitor._baseline.get_slot_baseline = MagicMock(return_value=baseline_value)
        return monitor, manager, notifier

    def test_skips_outside_trading_session(self) -> None:
        monitor, manager, notifier = self._make(
            phase=MarketPhase.LUNCH_BREAK, df=_live_df("2026-06-03", 9999), baseline_value=1000.0
        )
        stats = monitor.run_once()
        manager.get_intraday_kline.assert_not_called()
        notifier.send.assert_not_called()
        self.assertEqual(stats["hits"], 0)

    def test_surge_triggers_one_notification(self) -> None:
        monitor, manager, notifier = self._make(
            phase=MarketPhase.INTRADAY, df=_live_df("2026-06-03", 3000), baseline_value=1000.0
        )
        stats = monitor.run_once()
        self.assertEqual(stats["hits"], 1)
        notifier.send.assert_called_once()
        content = notifier.send.call_args.args[0]
        self.assertIn("600036", content)
        self.assertIn("放量", content)

    def test_dedup_same_stock_same_type_once_per_day(self) -> None:
        monitor, manager, notifier = self._make(
            phase=MarketPhase.INTRADAY, df=_live_df("2026-06-03", 3000), baseline_value=1000.0
        )
        monitor.run_once()
        monitor.run_once()
        self.assertEqual(notifier.send.call_count, 1)  # 第二轮被去重

    def test_no_hit_no_notification(self) -> None:
        monitor, manager, notifier = self._make(
            phase=MarketPhase.INTRADAY, df=_live_df("2026-06-03", 1000), baseline_value=1000.0
        )
        stats = monitor.run_once()
        self.assertEqual(stats["hits"], 0)
        notifier.send.assert_not_called()

    def test_skips_when_last_closed_bar_not_today(self) -> None:
        monitor, manager, notifier = self._make(
            phase=MarketPhase.INTRADAY, df=_live_df("2026-05-30", 3000), baseline_value=1000.0
        )
        stats = monitor.run_once()
        self.assertEqual(stats["hits"], 0)
        notifier.send.assert_not_called()

    def test_single_stock_failure_does_not_break_round(self) -> None:
        cfg = _cfg(stock_list=["600036", "000725"])
        manager = MagicMock()

        def _side_effect(code, period="5m", count=50):
            if code == "600036":
                raise RuntimeError("net error")
            return _live_df("2026-06-03", 3000)

        manager.get_intraday_kline.side_effect = _side_effect
        notifier = MagicMock()
        notifier.send.return_value = True
        monitor = IntradayVolumeMonitor(
            config_provider=lambda: cfg,
            manager=manager,
            notifier=notifier,
            phase_fn=lambda _n: MarketPhase.INTRADAY,
            now_fn=lambda: datetime(2026, 6, 3, 10, 6, 0),
        )
        monitor._baseline.get_slot_baseline = MagicMock(return_value=1000.0)
        stats = monitor.run_once()
        self.assertEqual(stats["hits"], 1)  # 000725 仍命中
        self.assertEqual(stats["errors"], 1)  # 600036 计入错误


if __name__ == "__main__":
    unittest.main()
