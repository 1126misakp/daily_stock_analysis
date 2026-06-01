# -*- coding: utf-8 -*-
"""
P1-3a 回归护栏：筹码分布链中 Tushare 先于 akshare 命中（机制①遍历链 priority）。

Tushare(priority=-1) < akshare(priority=1)，且 cyq_chips 实测可用，故 Tushare 天然优先。
本测试为零生产代码改动的断言守护，防止后续误调链顺序导致筹码回落 akshare。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from data_provider.base import DataFetcherManager
from data_provider.realtime_types import ChipDistribution
from data_provider.tushare_fetcher import TushareFetcher


def _chip(source: str) -> ChipDistribution:
    return ChipDistribution(code="600000", source=source, avg_cost=10.0, concentration_90=0.1)


class TestTushareChipPriority(unittest.TestCase):
    @patch("src.config.get_config")
    def test_chip_distribution_prefers_tushare_over_akshare(self, mock_get_config) -> None:
        mock_get_config.return_value = SimpleNamespace(enable_chip_distribution=True)

        tushare = MagicMock()
        tushare.name = "TushareFetcher"
        tushare.priority = -1
        tushare.get_chip_distribution.return_value = _chip("tushare")

        akshare = MagicMock()
        akshare.name = "AkshareFetcher"
        akshare.priority = 1
        akshare.get_chip_distribution.return_value = _chip("akshare")

        # TickFlow 无 get_chip_distribution，应被跳过（spec 限定属性）
        tickflow = MagicMock(spec=["name", "priority", "get_realtime_quote"])
        tickflow.name = "TickFlowFetcher"
        tickflow.priority = -2

        manager = DataFetcherManager(fetchers=[tickflow, tushare, akshare])

        chip = manager.get_chip_distribution("600000")

        self.assertIsNotNone(chip)
        self.assertEqual(chip.source, "tushare")
        tushare.get_chip_distribution.assert_called_once()
        akshare.get_chip_distribution.assert_not_called()


class TestTushareChipSourceLabel(unittest.TestCase):
    """筹码来自 Tushare 时 ChipDistribution.source 应标 'tushare'（修正既有默认 'akshare' 标签）。"""

    def test_tushare_chip_distribution_labels_source_tushare(self):
        f = TushareFetcher.__new__(TushareFetcher)
        f.rate_limit_per_minute = 80
        f._call_count = 0
        f._minute_start = None
        f._api = object()  # 非 None，绕过 None 守卫

        metrics = {
            "获利比例": 0.13, "平均成本": 11.58,
            "90成本-低": 9.0, "90成本-高": 14.1, "90集中度": 0.22,
            "70成本-低": 9.5, "70成本-高": 13.8, "70集中度": 0.18,
        }
        nonempty = pd.DataFrame({"close": [11.6]})
        with patch.object(f, "get_trade_time", return_value="20260529"), \
             patch.object(f, "_convert_stock_code", return_value="600000.SH"), \
             patch.object(f, "_call_api_with_rate_limit", return_value=nonempty), \
             patch.object(f, "compute_cyq_metrics", return_value=metrics):
            chip = f.get_chip_distribution("600000")

        self.assertIsNotNone(chip)
        self.assertEqual(chip.source, "tushare")


if __name__ == "__main__":
    unittest.main()
