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
        count = (self._baseline_days + 5) * _BARS_PER_DAY  # +5 余量应对个别股近期停牌
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
