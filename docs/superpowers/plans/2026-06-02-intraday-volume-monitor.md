# 盘中分钟级量能监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在交易时段每 5 分钟扫描"自选股∪持仓股"的 5 分钟 K 线成交量，与近 20 交易日同时段均量对比，识别放量(≥2.0x)/缩量(≤0.5x)异动，当日同股同类型仅首次，合并成一条飞书消息推送。

**Architecture:** 方案 A 独立监控器。新增 `src/services/intraday_volume_monitor.py` 主体 + `src/services/intraday_volume/` 子包（detector 纯计算 / baseline 同时段基线 / universe 标的解析），注册进现有 `src/scheduler.py` 后台任务框架（与 agent_event_monitor 并列）。取数只经 `DataFetcherManager.get_intraday_kline()`（内部仅路由 TickFlow，守数据源铁律）；推送走现有 `NotificationService`。纯后台，`.env` 开关，无前端、无新建数据库表（去重用内存当日集合）。

**Tech Stack:** Python 3 / pandas / unittest（pytest 运行）/ 现有 `data_provider`、`src.notification`、`src.core.trading_calendar`、`src.services.portfolio_service`。

**设计文档：** `docs/superpowers/specs/2026-06-02-intraday-volume-monitor-design.md`

---

## 文件结构

| 文件 | 职责 | 创建/修改 |
|------|------|-----------|
| `src/config.py` | 新增 7 个配置字段 + from_env 解析 | 修改 |
| `src/services/intraday_volume/__init__.py` | 子包导出 | 创建 |
| `src/services/intraday_volume/detector.py` | 纯函数 `classify()` + `VolumeSignal` | 创建 |
| `src/services/intraday_volume/baseline.py` | `compute_slot_baselines()` + `BaselineProvider` | 创建 |
| `src/services/intraday_volume/universe.py` | `resolve_universe()` 自选∪持仓去重 | 创建 |
| `src/services/intraday_volume_monitor.py` | `IntradayVolumeMonitor.run_once()` 编排 + 消息渲染 | 创建 |
| `main.py` | 在 background_tasks 注册块追加监控任务 | 修改 |
| `tests/test_intraday_volume_detector.py` | detector 单测 | 创建 |
| `tests/test_intraday_volume_baseline.py` | baseline 单测 | 创建 |
| `tests/test_intraday_volume_universe.py` | universe 单测 | 创建 |
| `tests/test_intraday_volume_monitor.py` | monitor 编排单测 | 创建 |

**关键数据契约：**
- `manager.get_intraday_kline(code, period="5m", count=N)` 返回 `pd.DataFrame`，列 `["code","datetime","open","high","low","close","volume","amount"]`，`datetime` 为字符串 `"YYYY-MM-DD HH:MM:SS"`；无数据返回 `None`。
- `VolumeSignal(signal_type: str, ratio: Optional[float], current_volume: float, baseline_volume: float)`，`signal_type ∈ {"surge","shrink","normal"}`。
- 一根的 **slot** = `datetime` 的 `HH:MM`（如 `"10:05"`）。

---

## Task 1: 新增配置字段与解析

**Files:**
- Modify: `src/config.py`（dataclass 字段区，约 724-726 行附近；from_env 解析区，约 1528-1534 行附近）
- Test: `tests/test_intraday_volume_config.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intraday_volume_config.py`：

```python
# -*- coding: utf-8 -*-
"""盘中量能监控配置解析测试。"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from src.config import Config


class IntradayVolumeConfigTestCase(unittest.TestCase):
    def test_defaults_when_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for k in [
                "INTRADAY_VOLUME_MONITOR_ENABLED",
                "INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES",
                "INTRADAY_VOLUME_SURGE_RATIO",
                "INTRADAY_VOLUME_SHRINK_RATIO",
                "INTRADAY_VOLUME_BASELINE_DAYS",
                "INTRADAY_VOLUME_BASELINE_MIN_SAMPLES",
                "INTRADAY_VOLUME_INCLUDE_HOLDINGS",
            ]:
                os.environ.pop(k, None)
            cfg = Config.from_env()
        self.assertFalse(cfg.intraday_volume_monitor_enabled)
        self.assertEqual(cfg.intraday_volume_monitor_interval_minutes, 5)
        self.assertEqual(cfg.intraday_volume_surge_ratio, 2.0)
        self.assertEqual(cfg.intraday_volume_shrink_ratio, 0.5)
        self.assertEqual(cfg.intraday_volume_baseline_days, 20)
        self.assertEqual(cfg.intraday_volume_baseline_min_samples, 5)
        self.assertTrue(cfg.intraday_volume_include_holdings)

    def test_parses_env_overrides(self) -> None:
        overrides = {
            "INTRADAY_VOLUME_MONITOR_ENABLED": "true",
            "INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES": "10",
            "INTRADAY_VOLUME_SURGE_RATIO": "3.0",
            "INTRADAY_VOLUME_SHRINK_RATIO": "0.4",
            "INTRADAY_VOLUME_BASELINE_DAYS": "30",
            "INTRADAY_VOLUME_BASELINE_MIN_SAMPLES": "8",
            "INTRADAY_VOLUME_INCLUDE_HOLDINGS": "false",
        }
        with patch.dict(os.environ, overrides, clear=False):
            cfg = Config.from_env()
        self.assertTrue(cfg.intraday_volume_monitor_enabled)
        self.assertEqual(cfg.intraday_volume_monitor_interval_minutes, 10)
        self.assertEqual(cfg.intraday_volume_surge_ratio, 3.0)
        self.assertEqual(cfg.intraday_volume_shrink_ratio, 0.4)
        self.assertEqual(cfg.intraday_volume_baseline_days, 30)
        self.assertEqual(cfg.intraday_volume_baseline_min_samples, 8)
        self.assertFalse(cfg.intraday_volume_include_holdings)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_intraday_volume_config.py -v`
Expected: FAIL（`AttributeError: ... 'intraday_volume_monitor_enabled'`）

- [ ] **Step 3: 加 dataclass 字段**

在 `src/config.py` 的 `agent_event_alert_rules_json: str = ""` 行之后追加：

```python
    # 盘中分钟级量能监控（独立于告警中心）
    intraday_volume_monitor_enabled: bool = False
    intraday_volume_monitor_interval_minutes: int = 5
    intraday_volume_surge_ratio: float = 2.0
    intraday_volume_shrink_ratio: float = 0.5
    intraday_volume_baseline_days: int = 20
    intraday_volume_baseline_min_samples: int = 5
    intraday_volume_include_holdings: bool = True
```

- [ ] **Step 4: 加 from_env 解析**

在 `src/config.py` 的 `from_env` 里 `agent_event_alert_rules_json=os.getenv('AGENT_EVENT_ALERT_RULES_JSON', ''),` 行之后追加：

```python
            intraday_volume_monitor_enabled=parse_env_bool(
                os.getenv('INTRADAY_VOLUME_MONITOR_ENABLED'), default=False
            ),
            intraday_volume_monitor_interval_minutes=parse_env_int(
                os.getenv('INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES'),
                5,
                field_name='INTRADAY_VOLUME_MONITOR_INTERVAL_MINUTES',
                minimum=1,
            ),
            intraday_volume_surge_ratio=parse_env_float(
                os.getenv('INTRADAY_VOLUME_SURGE_RATIO'),
                2.0,
                field_name='INTRADAY_VOLUME_SURGE_RATIO',
            ),
            intraday_volume_shrink_ratio=parse_env_float(
                os.getenv('INTRADAY_VOLUME_SHRINK_RATIO'),
                0.5,
                field_name='INTRADAY_VOLUME_SHRINK_RATIO',
            ),
            intraday_volume_baseline_days=parse_env_int(
                os.getenv('INTRADAY_VOLUME_BASELINE_DAYS'),
                20,
                field_name='INTRADAY_VOLUME_BASELINE_DAYS',
                minimum=1,
            ),
            intraday_volume_baseline_min_samples=parse_env_int(
                os.getenv('INTRADAY_VOLUME_BASELINE_MIN_SAMPLES'),
                5,
                field_name='INTRADAY_VOLUME_BASELINE_MIN_SAMPLES',
                minimum=1,
            ),
            intraday_volume_include_holdings=parse_env_bool(
                os.getenv('INTRADAY_VOLUME_INCLUDE_HOLDINGS'), default=True
            ),
```

> 注：`parse_env_bool` / `parse_env_int` / `parse_env_float` 已存在于 `src/config.py`（约 137/147/192 行）。`parse_env_int` 接受 `field_name` 与 `minimum`；`parse_env_float` 接受 `field_name`。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_intraday_volume_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 提交**

```bash
git add src/config.py tests/test_intraday_volume_config.py
git commit -m "feat(intraday-volume): 新增盘中量能监控配置字段与解析"
```

---

## Task 2: detector.py 量能判定（纯函数）

**Files:**
- Create: `src/services/intraday_volume/__init__.py`
- Create: `src/services/intraday_volume/detector.py`
- Test: `tests/test_intraday_volume_detector.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intraday_volume_detector.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_intraday_volume_detector.py -v`
Expected: FAIL（`ModuleNotFoundError: ... intraday_volume`）

- [ ] **Step 3: 实现子包与 detector**

创建 `src/services/intraday_volume/__init__.py`：

```python
# -*- coding: utf-8 -*-
"""盘中分钟级量能监控子包：纯计算与取数辅助。"""
```

创建 `src/services/intraday_volume/detector.py`：

```python
# -*- coding: utf-8 -*-
"""量能异动判定：纯函数，无 IO。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SIGNAL_SURGE = "surge"
SIGNAL_SHRINK = "shrink"
SIGNAL_NORMAL = "normal"


@dataclass
class VolumeSignal:
    signal_type: str
    ratio: Optional[float]
    current_volume: float
    baseline_volume: float


def classify(
    current_volume: float,
    baseline_volume: Optional[float],
    *,
    surge_ratio: float,
    shrink_ratio: float,
) -> VolumeSignal:
    """根据量比判定放量/缩量/正常。baseline 缺失或非正时一律 normal（不误报）。"""
    if baseline_volume is None or baseline_volume <= 0:
        return VolumeSignal(SIGNAL_NORMAL, None, float(current_volume), float(baseline_volume or 0.0))
    ratio = float(current_volume) / float(baseline_volume)
    if ratio >= surge_ratio:
        signal_type = SIGNAL_SURGE
    elif ratio <= shrink_ratio:
        signal_type = SIGNAL_SHRINK
    else:
        signal_type = SIGNAL_NORMAL
    return VolumeSignal(signal_type, ratio, float(current_volume), float(baseline_volume))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_intraday_volume_detector.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/services/intraday_volume/__init__.py src/services/intraday_volume/detector.py tests/test_intraday_volume_detector.py
git commit -m "feat(intraday-volume): detector 量能判定纯函数"
```

---

## Task 3: baseline.py 同时段历史基线

**Files:**
- Create: `src/services/intraday_volume/baseline.py`
- Test: `tests/test_intraday_volume_baseline.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intraday_volume_baseline.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_intraday_volume_baseline.py -v`
Expected: FAIL（`ModuleNotFoundError: ... baseline`）

- [ ] **Step 3: 实现 baseline.py**

创建 `src/services/intraday_volume/baseline.py`：

```python
# -*- coding: utf-8 -*-
"""同时段历史基线：每只股票近 N 交易日各 5 分钟时刻的均量。"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Set

import pandas as pd

logger = logging.getLogger(__name__)

# A 股一个交易日的 5 分钟 bar 数（4 小时 / 5 分钟）
_BARS_PER_DAY = 48


def _slot_of(datetime_str: object) -> str:
    """\"2026-06-03 10:05:00\" -> \"10:05\"。"""
    return str(datetime_str)[11:16]


def _date_of(datetime_str: object) -> str:
    """\"2026-06-03 10:05:00\" -> \"2026-06-03\"。"""
    return str(datetime_str)[0:10]


def compute_slot_baselines(
    df: Optional[pd.DataFrame], today_str: str, min_samples: int
) -> Dict[str, float]:
    """把历史（date < today）5m bar 按 slot 分组求均量；样本不足或均量非正则剔除。"""
    if df is None or getattr(df, "empty", True):
        return {}
    if "datetime" not in df.columns or "volume" not in df.columns:
        return {}
    work = df.copy()
    work["__slot"] = work["datetime"].map(_slot_of)
    work["__date"] = work["datetime"].map(_date_of)
    work = work[work["__date"] < today_str]
    out: Dict[str, float] = {}
    for slot, grp in work.groupby("__slot"):
        vols = pd.to_numeric(grp["volume"], errors="coerce").dropna()
        if len(vols) >= min_samples:
            mean = float(vols.mean())
            if mean > 0:
                out[slot] = mean
    return out


class BaselineProvider:
    """按需加载并当日缓存每只股票的 slot 基线。跨交易日调用 reset()。"""

    def __init__(self, manager, *, baseline_days: int, min_samples: int):
        self._manager = manager
        self._baseline_days = baseline_days
        self._min_samples = min_samples
        self._cache: Dict[str, Dict[str, float]] = {}
        self._missing: Set[str] = set()

    def reset(self) -> None:
        self._cache.clear()
        self._missing.clear()

    def get_slot_baseline(self, code: str, slot: str, today_str: str) -> Optional[float]:
        if code not in self._cache and code not in self._missing:
            self._load(code, today_str)
        return self._cache.get(code, {}).get(slot)

    def _load(self, code: str, today_str: str) -> None:
        count = (self._baseline_days + 2) * _BARS_PER_DAY
        try:
            df = self._manager.get_intraday_kline(code, period="5m", count=count)
        except Exception as exc:  # noqa: BLE001 - 取数失败不拖垮监控
            logger.warning("[IntradayVolume] 基线取数失败 %s: %s", code, exc)
            self._missing.add(code)
            return
        baselines = compute_slot_baselines(df, today_str, self._min_samples)
        if baselines:
            self._cache[code] = baselines
        else:
            self._missing.add(code)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_intraday_volume_baseline.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add src/services/intraday_volume/baseline.py tests/test_intraday_volume_baseline.py
git commit -m "feat(intraday-volume): baseline 同时段历史基线计算与缓存"
```

---

## Task 4: universe.py 标的解析（自选∪持仓）

**Files:**
- Create: `src/services/intraday_volume/universe.py`
- Test: `tests/test_intraday_volume_universe.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intraday_volume_universe.py`：

```python
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


if __name__ == "__main__":
    unittest.main()
```

> 注：`_holding_symbols()` 内部已自带 try/except 返回 `[]`；此处 `side_effect` 用于验证 `resolve_universe` 对异常的兜底（即便 `_holding_symbols` 抛出也不影响自选股）。

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_intraday_volume_universe.py -v`
Expected: FAIL（`ModuleNotFoundError: ... universe`）

- [ ] **Step 3: 实现 universe.py**

创建 `src/services/intraday_volume/universe.py`：

```python
# -*- coding: utf-8 -*-
"""监控标的解析：自选股 ∪ 持仓股，去重并规范化代码。"""
from __future__ import annotations

import logging
from typing import List, Sequence

from data_provider.base import normalize_stock_code

logger = logging.getLogger(__name__)


def resolve_universe(stock_list: Sequence[str], *, include_holdings: bool) -> List[str]:
    """返回去重后的监控代码列表，保持首次出现顺序。持仓读取失败时退化为仅自选股。"""
    codes: List[str] = []
    seen = set()

    def _add(raw: str) -> None:
        try:
            code = normalize_stock_code(raw)
        except Exception:  # noqa: BLE001
            return
        if code and code not in seen:
            seen.add(code)
            codes.append(code)

    for raw in stock_list or []:
        _add(raw)

    if include_holdings:
        try:
            for sym in _holding_symbols():
                _add(sym)
        except Exception as exc:  # noqa: BLE001 - 持仓异常不阻断监控
            logger.warning("[IntradayVolume] 合并持仓股失败，仅用自选股: %s", exc)

    return codes


def _holding_symbols() -> List[str]:
    """读取所有活跃账户的持仓代码；任何异常返回空列表。"""
    try:
        from src.services.portfolio_service import PortfolioService

        snapshot = PortfolioService().get_portfolio_snapshot()
        out: List[str] = []
        for account in snapshot.get("accounts", []):
            for position in account.get("positions", []):
                symbol = position.get("symbol")
                if symbol:
                    out.append(symbol)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IntradayVolume] 读取持仓快照失败: %s", exc)
        return []
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_intraday_volume_universe.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 提交**

```bash
git add src/services/intraday_volume/universe.py tests/test_intraday_volume_universe.py
git commit -m "feat(intraday-volume): universe 自选∪持仓标的解析"
```

---

## Task 5: intraday_volume_monitor.py 编排 + 消息渲染

**Files:**
- Create: `src/services/intraday_volume_monitor.py`
- Test: `tests/test_intraday_volume_monitor.py`

设计要点（实现时遵守）：
- 构造参数：`config_provider`（无参 callable，返回最新 `Config`，用于热读配置），可选注入 `manager` / `notifier` / `phase_fn` / `now_fn`（便于测试）。
- `phase_fn(now)` 默认 `lambda now: infer_market_phase("CN", now)`；`now_fn()` 默认 `lambda: get_market_now("CN")`。
- 运行时段：`phase ∈ {MarketPhase.INTRADAY, MarketPhase.CLOSING_AUCTION}` 才扫描，否则返回零统计。
- 跨交易日重置：`now_fn().date()` 变化时，`baseline_provider.reset()` + 清空当日去重集合。
- "当前根"：对每只股票拉 `get_intraday_kline(code, "5m", count=50)`，取 `iloc[-2]`（最后一根**已收**的 bar）；要求该 bar 的日期 == 当日市场日期，否则跳过（本会话尚无已收 bar）。
- 去重：内存 `set` of `(code, signal_type)`，当日仅首次；已加入即便推送失败也不回滚。
- 推送：本轮有命中才 `NotificationService().send(content, route_type="alert", severity="info")`。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_intraday_volume_monitor.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_intraday_volume_monitor.py -v`
Expected: FAIL（`ModuleNotFoundError: ... intraday_volume_monitor`）

- [ ] **Step 3: 实现 monitor**

创建 `src/services/intraday_volume_monitor.py`：

```python
# -*- coding: utf-8 -*-
"""盘中分钟级量能监控器：编排一轮扫描并合并推送。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set, Tuple

from src.core.trading_calendar import MarketPhase, get_market_now, infer_market_phase
from src.services.intraday_volume.baseline import BaselineProvider, _slot_of, _date_of
from src.services.intraday_volume.detector import (
    SIGNAL_SHRINK,
    SIGNAL_SURGE,
    VolumeSignal,
    classify,
)
from src.services.intraday_volume.universe import resolve_universe

logger = logging.getLogger(__name__)

_RUN_PHASES = {MarketPhase.INTRADAY, MarketPhase.CLOSING_AUCTION}
_LIVE_PROBE_COUNT = 50
_SIGNAL_LABEL = {SIGNAL_SURGE: "放量", SIGNAL_SHRINK: "缩量"}
_SIGNAL_EMOJI = {SIGNAL_SURGE: "🔴", SIGNAL_SHRINK: "🔵"}


class IntradayVolumeMonitor:
    def __init__(
        self,
        config_provider: Callable[[], object],
        *,
        manager=None,
        notifier=None,
        phase_fn: Optional[Callable[[datetime], MarketPhase]] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self._config_provider = config_provider
        self._manager = manager
        self._notifier = notifier
        self._phase_fn = phase_fn or (lambda now: infer_market_phase("CN", now))
        self._now_fn = now_fn or (lambda: get_market_now("CN"))

        cfg = config_provider()
        self._baseline = BaselineProvider(
            self._get_manager(),
            baseline_days=int(getattr(cfg, "intraday_volume_baseline_days", 20)),
            min_samples=int(getattr(cfg, "intraday_volume_baseline_min_samples", 5)),
        )
        self._alerted: Set[Tuple[str, str]] = set()
        self._cache_date: Optional[str] = None

    # --- 懒构建依赖（默认走真实实现）---
    def _get_manager(self):
        if self._manager is None:
            from data_provider import DataFetcherManager

            self._manager = DataFetcherManager()
        return self._manager

    def _get_notifier(self):
        if self._notifier is None:
            from src.notification import NotificationService

            self._notifier = NotificationService()
        return self._notifier

    def run_once(self) -> Dict[str, int]:
        stats = {"scanned": 0, "hits": 0, "skipped": 0, "errors": 0, "notified": 0}
        try:
            now = self._now_fn()
            phase = self._phase_fn(now)
            if phase not in _RUN_PHASES:
                return stats

            today_str = now.strftime("%Y-%m-%d")
            self._roll_day(today_str)

            cfg = self._config_provider()
            codes = resolve_universe(
                getattr(cfg, "stock_list", []) or [],
                include_holdings=bool(getattr(cfg, "intraday_volume_include_holdings", True)),
            )
            surge_ratio = float(getattr(cfg, "intraday_volume_surge_ratio", 2.0))
            shrink_ratio = float(getattr(cfg, "intraday_volume_shrink_ratio", 0.5))

            hits: List[Dict[str, object]] = []
            for code in codes:
                stats["scanned"] += 1
                outcome = self._scan_one(code, today_str, surge_ratio, shrink_ratio)
                if outcome is None:
                    stats["skipped"] += 1
                    continue
                if outcome == "error":
                    stats["errors"] += 1
                    continue
                hits.append(outcome)
                stats["hits"] += 1

            if hits:
                content = self._render(now, hits)
                ok = False
                try:
                    ok = self._get_notifier().send(content, route_type="alert", severity="info")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[IntradayVolume] 飞书推送异常: %s", exc)
                if ok:
                    stats["notified"] = 1
                else:
                    logger.warning("[IntradayVolume] 本轮 %d 条命中推送失败（当日不补推）", len(hits))
        except Exception as exc:  # noqa: BLE001 - 绝不让后台任务崩溃
            logger.warning("[IntradayVolume] run_once 异常: %s", exc, exc_info=True)
        return stats

    def _roll_day(self, today_str: str) -> None:
        if self._cache_date != today_str:
            self._cache_date = today_str
            self._baseline.reset()
            self._alerted.clear()

    def _scan_one(self, code, today_str, surge_ratio, shrink_ratio):
        """返回 hit dict / None(跳过) / "error"。"""
        try:
            df = self._get_manager().get_intraday_kline(code, period="5m", count=_LIVE_PROBE_COUNT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[IntradayVolume] 取数失败 %s: %s", code, exc)
            return "error"
        if df is None or getattr(df, "empty", True) or len(df) < 2:
            return None
        bar = df.iloc[-2]  # 最后一根已收 bar
        if _date_of(bar["datetime"]) != today_str:
            return None  # 本会话尚无已收 bar
        slot = _slot_of(bar["datetime"])
        baseline = self._baseline.get_slot_baseline(code, slot, today_str)
        if baseline is None:
            return None
        current_volume = float(bar["volume"])
        signal: VolumeSignal = classify(
            current_volume, baseline, surge_ratio=surge_ratio, shrink_ratio=shrink_ratio
        )
        if signal.signal_type not in _SIGNAL_LABEL:
            return None
        key = (code, signal.signal_type)
        if key in self._alerted:
            return None
        self._alerted.add(key)
        return {
            "code": code,
            "signal_type": signal.signal_type,
            "ratio": signal.ratio,
            "price": float(bar["close"]),
            "current_volume": current_volume,
            "baseline_volume": baseline,
        }

    def _render(self, now: datetime, hits: List[Dict[str, object]]) -> str:
        lines = [f"📊 盘中量能异动 {now.strftime('%H:%M')}（5分钟）"]
        for stype in (SIGNAL_SURGE, SIGNAL_SHRINK):
            group = [h for h in hits if h["signal_type"] == stype]
            if not group:
                continue
            lines.append(f"{_SIGNAL_EMOJI[stype]} {_SIGNAL_LABEL[stype]}")
            for h in group:
                lines.append(
                    f"  · {h['code']}  量比{h['ratio']:.1f}x  现价{h['price']:.2f}  "
                    f"量{h['current_volume']:.0f}(基线{h['baseline_volume']:.0f})"
                )
        return "\n".join(lines)
```

> 注：`_slot_of` / `_date_of` 复用自 `baseline.py`（模块内私有函数，monitor 直接 import 使用，避免重复实现）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_intraday_volume_monitor.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add src/services/intraday_volume_monitor.py tests/test_intraday_volume_monitor.py
git commit -m "feat(intraday-volume): monitor 编排扫描、去重、合并推送"
```

---

## Task 6: 注册后台任务到调度框架

**Files:**
- Modify: `main.py`（`--schedule` 模式的 `background_tasks` 注册块，约 958-976 行，紧邻 `agent_event_monitor` 注册之后）

- [ ] **Step 1: 阅读现有注册块**

Run: `sed -n '954,985p' main.py`
确认结构：`background_tasks = []` 后有 `if getattr(config, 'agent_event_monitor_enabled', False):` 注册块，最后 `run_with_schedule(..., background_tasks=background_tasks, ...)`。

- [ ] **Step 2: 追加监控任务注册**

在 `main.py` 中 `agent_event_monitor` 注册块（以 `background_tasks.append({...})` 结束、`name="agent_event_monitor"`）**之后、`run_with_schedule(` 之前**，插入：

```python
            if getattr(config, 'intraday_volume_monitor_enabled', False):
                from src.services.intraday_volume_monitor import IntradayVolumeMonitor

                iv_interval_minutes = max(
                    1, getattr(config, 'intraday_volume_monitor_interval_minutes', 5)
                )
                intraday_volume_monitor = IntradayVolumeMonitor(
                    config_provider=_reload_runtime_config
                )

                def intraday_volume_task():
                    stats = intraday_volume_monitor.run_once()
                    if stats.get("hits"):
                        logger.info(
                            "[IntradayVolume] 本轮命中 %d 条（已推送=%d）",
                            stats["hits"],
                            stats.get("notified", 0),
                        )

                background_tasks.append({
                    "task": intraday_volume_task,
                    "interval_seconds": iv_interval_minutes * 60,
                    "run_immediately": True,
                    "name": "intraday_volume_monitor",
                })
```

> `_reload_runtime_config` 是同作用域内已存在的配置热加载 callable（agent_event_monitor 也用它）。

- [ ] **Step 3: 语法与导入自检**

Run: `python -c "import ast; ast.parse(open('main.py').read()); print('main.py 语法 OK')"`
Expected: `main.py 语法 OK`

Run: `python -c "from src.services.intraday_volume_monitor import IntradayVolumeMonitor; print('import OK')"`
Expected: `import OK`

- [ ] **Step 4: 全量子包测试回归**

Run: `python -m pytest tests/test_intraday_volume_config.py tests/test_intraday_volume_detector.py tests/test_intraday_volume_baseline.py tests/test_intraday_volume_universe.py tests/test_intraday_volume_monitor.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add main.py
git commit -m "feat(intraday-volume): 注册盘中量能监控为调度后台任务"
```

---

## Task 7: CI 守绿 + 铁律烟雾

**Files:** 无（仅验证）

- [ ] **Step 1: 跑 CI 闸门**

Run: `./scripts/ci_gate.sh`
Expected: 绿（若该脚本存在；不存在则跑 `python -m pytest tests/ -q` 整体回归并确认无新增失败）

- [ ] **Step 2: 确认未触碰数据源铁律**

Run: `git diff --name-only origin/main...HEAD`
Expected: 改动文件**不含** `data_provider/base.py`、`data_provider/tickflow_fetcher.py`、`data_provider/tushare_fetcher.py` 等数据源路由/fetcher 文件（仅 `src/config.py`、`src/services/intraday_volume*`、`main.py`、`tests/*`、`docs/*`）。

- [ ] **Step 3: 无新增提交则跳过；如有修复则提交**

```bash
git add -A && git commit -m "test(intraday-volume): CI 守绿修复" || echo "无需修复"
```

---

## Self-Review（已执行）

- **Spec 覆盖**：标的范围(Task4)、5m颗粒度/频率(Task6/config)、同时段基线(Task3)、放量缩量阈值(Task2/config)、当日去重(Task5)、合并飞书推送(Task5)、纯后台.env开关(Task1/Task6)、运行时段gate(Task5)、未走完bar规避(Task5 `iloc[-2]`+当日校验)、429/单股失败容错(Task5)、不碰数据源铁律(Task7校验)。逐项有对应任务。
- **占位符扫描**：无 TBD/TODO；每个代码步给出完整可运行代码与确切命令。
- **类型/签名一致性**：`classify(current, baseline, *, surge_ratio, shrink_ratio)`、`VolumeSignal(signal_type, ratio, current_volume, baseline_volume)`、`BaselineProvider(manager, *, baseline_days, min_samples).get_slot_baseline(code, slot, today_str)/reset()`、`resolve_universe(stock_list, *, include_holdings)`、`IntradayVolumeMonitor(config_provider, *, manager, notifier, phase_fn, now_fn).run_once()` 在各任务间一致。
- **部署**：见设计文档 §8（`.env` 开 `INTRADAY_VOLUME_MONITOR_ENABLED=true` → 重建 analyzer 容器 → 盘中看日志 + 飞书核对）。
```
