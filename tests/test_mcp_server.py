# -*- coding: utf-8 -*-
"""MCP server：白名单注册表(恰好30个/排除项缺席) + 派发。"""
import json
import unittest
from unittest.mock import patch

import api.mcp.server as mcpsrv


class TestMcpRegistry(unittest.TestCase):
    def test_registry_has_exactly_30_tools(self):
        reg = mcpsrv.build_mcp_registry()
        self.assertEqual(len(reg), 30)

    def test_excluded_tools_absent(self):
        names = set(mcpsrv.build_mcp_registry().list_names())
        for blocked in ("get_portfolio_snapshot", "get_analysis_context",
                        "get_stock_backtest_summary", "get_skill_backtest_summary",
                        "get_strategy_backtest_summary"):
            self.assertNotIn(blocked, names)

    def test_key_tools_present(self):
        names = set(mcpsrv.build_mcp_registry().list_names())
        for t in ("get_realtime_quote", "search_stock_news", "get_dragon_tiger",
                  "get_income_statement", "get_risk_assessment"):
            self.assertIn(t, names)

    def test_no_duplicate_names_across_sources(self):
        """防止新增工具与镜像工具撞名被 register 静默覆盖（计数仍 30 却少了一个）。"""
        from src.agent.tools.data_tools import ALL_DATA_TOOLS
        from src.agent.tools.analysis_tools import ALL_ANALYSIS_TOOLS
        from src.agent.tools.market_tools import ALL_MARKET_TOOLS
        from src.agent.tools.search_tools import ALL_SEARCH_TOOLS
        from src.agent.tools.dataset_tools import ALL_DATASET_TOOLS
        names = [t.name for t in (ALL_DATA_TOOLS + ALL_ANALYSIS_TOOLS + ALL_MARKET_TOOLS
                                  + ALL_SEARCH_TOOLS + ALL_DATASET_TOOLS)
                 if t.name not in mcpsrv.EXCLUDED_TOOL_NAMES]
        self.assertEqual(len(names), len(set(names)), f"duplicate tool names: {names}")

    def test_list_tools_payload_shape(self):
        tools = mcpsrv.build_mcp_registry().to_mcp_tools()
        self.assertEqual(len(tools), 30)
        self.assertTrue(all({"name", "description", "inputSchema"} <= set(t) for t in tools))

    def test_dispatch_routes_to_handler(self):
        reg = mcpsrv.build_mcp_registry()
        with patch.object(reg, "execute", return_value={"ok": 1}) as ex:
            out = mcpsrv._dispatch(reg, "get_realtime_quote", {"stock_code": "600519"})
        self.assertEqual(out, {"ok": 1})
        ex.assert_called_once_with("get_realtime_quote", stock_code="600519")

    def test_dispatch_unknown_tool(self):
        reg = mcpsrv.build_mcp_registry()
        out = mcpsrv._dispatch(reg, "no_such_tool", {})
        self.assertIn("error", out)

    def test_dispatch_handler_exception(self):
        reg = mcpsrv.build_mcp_registry()
        with patch.object(reg, "execute", side_effect=RuntimeError("boom")):
            out = mcpsrv._dispatch(reg, "get_realtime_quote", {"stock_code": "x"})
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
