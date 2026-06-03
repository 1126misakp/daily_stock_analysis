# -*- coding: utf-8 -*-
"""监控标的解析测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services.intraday_volume.universe import resolve_universe


class ResolveUniverseTestCase(unittest.TestCase):
    def test_stock_list_only_when_holdings_disabled(self) -> None:
        codes = resolve_universe(["600036", "000725"], include_holdings=False)
        self.assertEqual(codes, ["600036", "000725"])

    def test_union_dedup_with_holdings(self) -> None:
        with patch(
            "src.services.intraday_volume.universe._holding_symbols",
            return_value=["000725", "002415"],
        ):
            codes = resolve_universe(["600036", "000725"], include_holdings=True)
        # 600036、000725（去重）、002415，保持出现顺序
        self.assertEqual(codes, ["600036", "000725", "002415"])

    def test_holdings_failure_degrades_to_stock_list(self) -> None:
        with patch(
            "src.services.intraday_volume.universe._holding_symbols",
            side_effect=RuntimeError("db down"),
        ):
            codes = resolve_universe(["600036"], include_holdings=True)
        self.assertEqual(codes, ["600036"])

    def test_holding_symbols_filters_non_cn(self) -> None:
        from src.services.intraday_volume import universe as uni

        snap = {
            "accounts": [
                {
                    "market": "cn",
                    "positions": [
                        {"symbol": "600036", "market": "cn"},
                        {"symbol": "HK00700", "market": "hk"},
                        {"symbol": "AAPL", "market": "us"},
                    ],
                }
            ]
        }
        # PortfolioService 在函数内 import，需在源模块处 patch
        with patch("src.services.portfolio_service.PortfolioService") as MockPS:
            MockPS.return_value.get_portfolio_snapshot.return_value = snap
            syms = uni._holding_symbols()
        self.assertEqual(syms, ["600036"])  # hk/us 被过滤


if __name__ == "__main__":
    unittest.main()
