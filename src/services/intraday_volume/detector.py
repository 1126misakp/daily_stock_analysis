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
