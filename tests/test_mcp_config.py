# -*- coding: utf-8 -*-
"""确认 MCP 配置字段存在、有安全默认，且能从环境变量真实读取。"""
import unittest

from src.config import Config, get_config


class TestMcpConfigFields(unittest.TestCase):
    def test_fields_exist_with_safe_defaults(self):
        cfg = get_config()
        self.assertTrue(hasattr(cfg, "mcp_api_keys"))
        self.assertTrue(hasattr(cfg, "mcp_dns_rebinding_protection"))
        self.assertTrue(hasattr(cfg, "mcp_allowed_hosts"))
        self.assertFalse(bool(cfg.mcp_dns_rebinding_protection))  # 默认关闭

    def test_reads_from_environment(self):
        """漏改 _load_from_env 时此用例必失败（只改声明无法从环境读到值）。"""
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {"MCP_API_KEYS": "k1:alice",
                                     "MCP_DNS_REBINDING_PROTECTION": "true"}):
            Config.reset_instance()
            try:
                cfg = get_config()
                self.assertEqual(cfg.mcp_api_keys, "k1:alice")
                self.assertTrue(bool(cfg.mcp_dns_rebinding_protection))
            finally:
                Config.reset_instance()  # 还原单例，避免污染其他测试


if __name__ == "__main__":
    unittest.main()
