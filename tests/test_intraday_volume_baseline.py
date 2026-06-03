# -*- coding: utf-8 -*-
"""同时段历史基线测试。"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

import pandas as pd

from src.services.intraday_volume.baseline import (
    BaselineProvider,
    compute_slot_baselines,
)


def _df(rows):
    return pd.DataFrame(rows, columns=["code", "datetime", "open", "high", "low", "close", "volume", "amount"])


class ComputeSlotBaselinesTestCase(unittest.TestCase):
    def test_groups_by_slot_excluding_today(self) -> None:
        rows = [
            ["600036", "2026-06-01 10:05:00", 0, 0, 0, 0, 100, 0],
            ["600036", "2026-06-02 10:05:00", 0, 0, 0, 0, 300, 0],
            ["600036", "2026-06-03 10:05:00", 0, 0, 0, 0, 9999, 0],  # today, excluded
            ["600036", "2026-06-01 10:10:00", 0, 0, 0, 0, 50, 0],
        ]
        out = compute_slot_baselines(_df(rows), today_str="2026-06-03", min_samples=2)
        self.assertAlmostEqual(out["10:05"], 200.0)  # (100+300)/2
        self.assertNotIn("10:10", out)  # only 1 sample < min_samples

    def test_empty_or_missing_columns(self) -> None:
        self.assertEqual(compute_slot_baselines(None, "2026-06-03", 2), {})
        self.assertEqual(compute_slot_baselines(_df([]), "2026-06-03", 2), {})


class BaselineProviderTestCase(unittest.TestCase):
    def test_loads_caches_and_returns_slot(self) -> None:
        rows = [
            ["600036", "2026-06-01 10:05:00", 0, 0, 0, 0, 100, 0],
            ["600036", "2026-06-02 10:05:00", 0, 0, 0, 0, 300, 0],
        ]
        manager = MagicMock()
        manager.get_intraday_kline.return_value = _df(rows)
        provider = BaselineProvider(manager, baseline_days=20, min_samples=2)
        self.assertAlmostEqual(provider.get_slot_baseline("600036", "10:05", "2026-06-03"), 200.0)
        # 第二次调用走缓存，不再请求 manager
        provider.get_slot_baseline("600036", "10:05", "2026-06-03")
        self.assertEqual(manager.get_intraday_kline.call_count, 1)

    def test_insufficient_data_marks_missing_and_returns_none(self) -> None:
        manager = MagicMock()
        manager.get_intraday_kline.return_value = None
        provider = BaselineProvider(manager, baseline_days=20, min_samples=2)
        self.assertIsNone(provider.get_slot_baseline("600036", "10:05", "2026-06-03"))
        provider.get_slot_baseline("600036", "10:05", "2026-06-03")
        self.assertEqual(manager.get_intraday_kline.call_count, 1)  # missing 也缓存，不重复请求

    def test_reset_clears_cache(self) -> None:
        manager = MagicMock()
        manager.get_intraday_kline.return_value = _df([
            ["600036", "2026-06-01 10:05:00", 0, 0, 0, 0, 100, 0],
            ["600036", "2026-06-02 10:05:00", 0, 0, 0, 0, 300, 0],
        ])
        provider = BaselineProvider(manager, baseline_days=20, min_samples=2)
        provider.get_slot_baseline("600036", "10:05", "2026-06-03")
        provider.reset()
        provider.get_slot_baseline("600036", "10:05", "2026-06-04")
        self.assertEqual(manager.get_intraday_kline.call_count, 2)


if __name__ == "__main__":
    unittest.main()
