# -*- coding: utf-8 -*-
"""MCPKeyService：读当前 key / 重置生成新 key 覆盖写回 / 文件权限 600。"""
import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.core.config_manager import ConfigManager
from src.services.mcp_key_service import MCPKeyService


class TestMCPKeyService(unittest.TestCase):
    def _service(self, initial):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env_path = Path(self.tmp.name) / ".env"
        body = f"MCP_API_KEYS={initial}\n" if initial is not None else "OTHER=1\n"
        env_path.write_text(body, encoding="utf-8")
        os.chmod(env_path, 0o600)
        return MCPKeyService(ConfigManager(env_path=env_path)), env_path

    def test_get_current_key_returns_first(self):
        svc, _ = self._service("abc123:default")
        self.assertEqual(svc.get_current_key(), "abc123")

    def test_get_current_key_none_when_absent(self):
        svc, _ = self._service(None)
        self.assertIsNone(svc.get_current_key())

    def test_reset_generates_and_overwrites(self):
        svc, env_path = self._service("oldkey:default")
        new_key = svc.reset_key()
        self.assertNotEqual(new_key, "oldkey")
        self.assertGreaterEqual(len(new_key), 32)
        # 落盘且整体覆盖（旧 key 不再存在）
        self.assertEqual(svc.get_current_key(), new_key)
        body = env_path.read_text(encoding="utf-8")
        self.assertNotIn("oldkey", body)
        self.assertIn("MCP_API_KEYS=", body)  # 键名仍全大写（apply_updates 会 key.upper()）

    def test_reset_keeps_permission_600(self):
        svc, env_path = self._service("oldkey")
        svc.reset_key()
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_reset_first_time_when_no_key(self):
        svc, _ = self._service(None)
        new_key = svc.reset_key()
        self.assertEqual(svc.get_current_key(), new_key)


if __name__ == "__main__":
    unittest.main()
