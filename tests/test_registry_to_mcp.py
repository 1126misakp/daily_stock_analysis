# -*- coding: utf-8 -*-
"""ToolDefinition/ToolRegistry 的 MCP schema 转换测试。"""
import unittest

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolRegistry


def _sample_tool() -> ToolDefinition:
    return ToolDefinition(
        name="get_demo",
        description="Demo tool",
        parameters=[
            ToolParameter(name="stock_code", type="string", description="A股代码", required=True),
            ToolParameter(name="days", type="integer", description="天数", required=False, default=30),
            ToolParameter(name="region", type="string", description="市场", required=False,
                          enum=["cn", "hk", "us"]),
        ],
        handler=lambda stock_code, days=30, region="cn": {"ok": True},
        category="data",
    )


class TestToolToMcp(unittest.TestCase):
    def test_to_mcp_tool_shape(self):
        d = _sample_tool().to_mcp_tool()
        self.assertEqual(d["name"], "get_demo")
        self.assertEqual(d["description"], "Demo tool")
        schema = d["inputSchema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("stock_code", schema["properties"])
        self.assertEqual(schema["properties"]["stock_code"]["type"], "string")
        self.assertEqual(schema["properties"]["region"]["enum"], ["cn", "hk", "us"])
        self.assertEqual(schema["required"], ["stock_code"])

    def test_registry_to_mcp_tools(self):
        reg = ToolRegistry()
        reg.register(_sample_tool())
        tools = reg.to_mcp_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "get_demo")


if __name__ == "__main__":
    unittest.main()
