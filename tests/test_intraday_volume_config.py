# -*- coding: utf-8 -*-
"""盘中量能监控配置解析测试。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.config import Config

# 计划中的 Config.from_env() 在本仓库不存在，真实工厂方法为 Config._load_from_env()，
# 现有 tests/test_config_env_compat.py 即按此约定（mock setup_env / _parse_litellm_yaml +
# patch.dict(os.environ, clear=True) + reset_instance）。这里沿用同一约定。


class IntradayVolumeConfigTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        Config.reset_instance()

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_defaults_when_unset(self, _mock_yaml, _mock_setup_env) -> None:
        with patch.dict(os.environ, {}, clear=True):
            cfg = Config._load_from_env()
        self.assertFalse(cfg.intraday_volume_monitor_enabled)
        self.assertEqual(cfg.intraday_volume_monitor_interval_minutes, 5)
        self.assertEqual(cfg.intraday_volume_surge_ratio, 2.0)
        self.assertEqual(cfg.intraday_volume_shrink_ratio, 0.5)
        self.assertEqual(cfg.intraday_volume_baseline_days, 20)
        self.assertEqual(cfg.intraday_volume_baseline_min_samples, 5)
        self.assertTrue(cfg.intraday_volume_include_holdings)

    @patch("src.config.setup_env")
    @patch.object(Config, "_parse_litellm_yaml", return_value=[])
    def test_parses_env_overrides(self, _mock_yaml, _mock_setup_env) -> None:
        overrides = {
            "INTRADAY_VOLUME_MONITOR_ENABLED": "true",
            "INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES": "10",
            "INTRADAY_VOLUME_SURGE_RATIO": "3.0",
            "INTRADAY_VOLUME_SHRINK_RATIO": "0.4",
            "INTRADAY_VOLUME_BASELINE_DAYS": "30",
            "INTRADAY_VOLUME_BASELINE_MIN_SAMPLES": "8",
            "INTRADAY_VOLUME_INCLUDE_HOLDINGS": "false",
        }
        with patch.dict(os.environ, overrides, clear=True):
            cfg = Config._load_from_env()
        self.assertTrue(cfg.intraday_volume_monitor_enabled)
        self.assertEqual(cfg.intraday_volume_monitor_interval_minutes, 10)
        self.assertEqual(cfg.intraday_volume_surge_ratio, 3.0)
        self.assertEqual(cfg.intraday_volume_shrink_ratio, 0.4)
        self.assertEqual(cfg.intraday_volume_baseline_days, 30)
        self.assertEqual(cfg.intraday_volume_baseline_min_samples, 8)
        self.assertFalse(cfg.intraday_volume_include_holdings)


if __name__ == "__main__":
    unittest.main()
