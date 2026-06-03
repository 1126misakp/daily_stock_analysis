# -*- coding: utf-8 -*-
"""量能判定纯函数测试。"""
from __future__ import annotations

import unittest

from src.services.intraday_volume.detector import (
    SIGNAL_NORMAL,
    SIGNAL_SHRINK,
    SIGNAL_SURGE,
    classify,
)


class ClassifyTestCase(unittest.TestCase):
    def test_surge_on_or_above_threshold(self) -> None:
        sig = classify(2000.0, 1000.0, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_SURGE)
        self.assertAlmostEqual(sig.ratio, 2.0)

    def test_just_below_surge_is_normal(self) -> None:
        sig = classify(1999.0, 1000.0, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_NORMAL)

    def test_shrink_on_or_below_threshold(self) -> None:
        sig = classify(500.0, 1000.0, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_SHRINK)

    def test_just_above_shrink_is_normal(self) -> None:
        sig = classify(510.0, 1000.0, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_NORMAL)

    def test_zero_baseline_is_normal_and_safe(self) -> None:
        sig = classify(1000.0, 0.0, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_NORMAL)
        self.assertIsNone(sig.ratio)

    def test_none_baseline_is_normal_and_safe(self) -> None:
        sig = classify(1000.0, None, surge_ratio=2.0, shrink_ratio=0.5)
        self.assertEqual(sig.signal_type, SIGNAL_NORMAL)
        self.assertIsNone(sig.ratio)


if __name__ == "__main__":
    unittest.main()
