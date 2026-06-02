# -*- coding: utf-8 -*-
"""自研选股：8 个策略的全市场量化 scorer。量化判据取自 strategies/<id>.yaml。"""
from __future__ import annotations

from typing import Callable, Dict

import pandas as pd

from .market_data import MarketPanel


def _emit(df: pd.DataFrame, score: pd.Series, detail: str) -> pd.DataFrame:
    hit = df[score > 0].copy()
    if hit.empty:
        return pd.DataFrame(columns=["code", "name", "signal_score", "signal_detail"])
    out = pd.DataFrame({
        "code": hit.index, "name": hit["name"].values,
        "signal_score": score[score > 0].values, "signal_detail": detail,
    })
    return out.reset_index(drop=True)


def ma_golden_cross(panel: MarketPanel) -> pd.DataFrame:
    df = panel.latest
    cross = (df["ma5_prev"] <= df["ma10_prev"]) & (df["ma5"] > df["ma10"])
    cond = cross & (df["vol_ratio"] > 1.2) & (df["bias_ma5"].abs() < 0.05)
    score = cond.astype(float) * (10 + (df["vol_ratio"].clip(upper=3) - 1.2) * 2)
    return _emit(df, score.where(cond, 0), "均线金叉：MA5上穿MA10，量比放大")


def volume_breakout(panel: MarketPanel) -> pd.DataFrame:
    df = panel.latest
    cond = (df["close"] >= df["high_20"]) & (df["vol_ratio"] > 2.0) & (df["bias_ma5"] < 0.05)
    score = cond.astype(float) * (12 + (df["vol_ratio"].clip(upper=5) - 2) * 1.5)
    return _emit(df, score.where(cond, 0), "放量突破：站上20日高点且量能>2倍")


def bottom_volume(panel: MarketPanel) -> pd.DataFrame:
    df = panel.latest
    cond = (df["ret_from_high20"] <= -0.15) & (df["vol_ratio"] > 3.0) & (df["close"] > df["open_"])
    score = cond.astype(float) * 8.0
    return _emit(df, score.where(cond, 0), "底部放量：深跌后放量收阳，潜在反转")


def shrink_pullback(panel: MarketPanel) -> pd.DataFrame:
    df = panel.latest
    bull = (df["ma5"] > df["ma10"]) & (df["ma10"] > df["ma20"])
    cond = bull & (df["vol_ratio"] < 0.7) & (df["bias_ma5"].abs() < 0.02)
    score = cond.astype(float) * 10.0
    return _emit(df, score.where(cond, 0), "缩量回踩：多头排列下缩量回踩MA5")


def bull_trend(panel: MarketPanel) -> pd.DataFrame:
    df = panel.latest
    cond = (df["ma5"] >= df["ma10"]) & (df["ma10"] >= df["ma20"]) & (df["close"] >= df["ma20"])
    score = cond.astype(float) * (12 - df["bias_ma5"].clip(lower=0) * 20)  # 乖离越大分越低（不追高）
    return _emit(df, score.where(cond, 0), "多头趋势：均线多头排列")


STRATEGY_SCORERS: Dict[str, Callable[[MarketPanel], pd.DataFrame]] = {
    "ma_golden_cross": ma_golden_cross,
    "volume_breakout": volume_breakout,
    "bottom_volume": bottom_volume,
    "shrink_pullback": shrink_pullback,
    "bull_trend": bull_trend,
}
