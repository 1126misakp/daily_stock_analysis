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


def one_yang_three_yin(panel: MarketPanel) -> pd.DataFrame:
    hits = []
    for code, g in panel.history.items():
        if len(g) < 5:
            continue
        w = g.tail(5).reset_index(drop=True)
        d1, d2, d3, d4, d5 = (w.iloc[i] for i in range(5))
        body1 = (d1["close"] - d1["open"]) / d1["open"] if d1["open"] else 0
        yang1 = body1 > 0.02
        mids_ok = all(d["low"] >= d1["open"] for d in (d2, d3, d4)) and \
            all(d["close"] <= d1["close"] for d in (d2, d3, d4))
        yang5 = d5["close"] > d1["close"] and d5["close"] > d5["open"]
        if yang1 and mids_ok and yang5:
            name = panel.names.get(code, code)
            hits.append({"code": code, "name": name, "signal_score": 15.0,
                         "signal_detail": "一阳夹三阴：整理形态完成，趋势延续入场"})
    return pd.DataFrame(hits, columns=["code", "name", "signal_score", "signal_detail"])


def box_oscillation(panel: MarketPanel) -> pd.DataFrame:
    hits = []
    for code, g in panel.history.items():
        if len(g) < 30:
            continue
        win = g.tail(60)   # 与 SNAPSHOT_DAYS=60 对齐；箱体看约 60 个交易日(~3个月)
        top = float(win["high"].max())
        bottom = float(win["low"].min())
        if bottom <= 0:
            continue
        width = (top - bottom) / bottom
        if not (0.05 <= width <= 0.50):     # 太窄无空间，太宽非箱体
            continue
        price = float(g.iloc[-1]["close"])
        dist_to_bottom = (price - bottom) / bottom
        # 贴底阈值由 ≤5% 收紧到 ≤4%；越贴近箱底分越高，
        # 便于截断 Top80 时优先取到最贴底的标的
        if 0 <= dist_to_bottom <= 0.04:
            name = panel.names.get(code, code)
            score = 10.0 + (0.04 - dist_to_bottom) / 0.04 * 5
            hits.append({"code": code, "name": name, "signal_score": score,
                         "signal_detail": f"箱体震荡：现价贴近箱底（{bottom:.2f}~{top:.2f}）"})
    return pd.DataFrame(hits, columns=["code", "name", "signal_score", "signal_detail"])


def growth_quality(panel: MarketPanel) -> pd.DataFrame:
    """第1段仅按估值/市值粗筛出'值得 LLM 深看的成长候选'，成长性判断交第2段 LLM。"""
    df = panel.latest
    pe = pd.to_numeric(df.get("pe"), errors="coerce")
    total_mv = pd.to_numeric(df.get("total_mv"), errors="coerce")  # 单位：万元
    # 收紧足切：PE 区间 (0, 40]、市值 > 50 亿（total_mv 单位万元）
    cond = (pe > 0) & (pe <= 40) & (total_mv > 5e6)
    score = cond.astype(float) * (15 - (pe.clip(upper=80) / 80) * 5)
    return _emit(df, score.where(cond, 0), "成长质量：盈利且估值合理，待LLM核成长")


STRATEGY_SCORERS: Dict[str, Callable[[MarketPanel], pd.DataFrame]] = {
    "ma_golden_cross": ma_golden_cross,
    "volume_breakout": volume_breakout,
    "bottom_volume": bottom_volume,
    "shrink_pullback": shrink_pullback,
    "bull_trend": bull_trend,
}

STRATEGY_SCORERS.update({
    "one_yang_three_yin": one_yang_three_yin,
    "box_oscillation": box_oscillation,
    "growth_quality": growth_quality,
})
