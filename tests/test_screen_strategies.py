# -*- coding: utf-8 -*-
import pandas as pd

from src.services.stock_screener.market_data import MarketPanel
from src.services.stock_screener import strategies as st


def _panel(latest_rows, history=None):
    latest = pd.DataFrame(latest_rows).set_index("code")
    return MarketPanel(trade_date="20260602", latest=latest,
                       history=history or {}, basic=pd.DataFrame(),
                       names={r["code"]: r["name"] for r in latest_rows},
                       industry={r["code"]: r.get("industry", "") for r in latest_rows})


def test_ma_golden_cross_hits():
    # 命中：今日 ma5>ma10 且昨日 ma5<=ma10（金叉），量比>1.2，乖离<5%
    rows = [{"code": "000001", "name": "A", "close": 10.2, "ma5": 10.1, "ma10": 10.0,
             "ma5_prev": 9.9, "ma10_prev": 10.0, "vol_ratio": 1.5, "bias_ma5": 0.01},
            {"code": "000002", "name": "B", "close": 10.2, "ma5": 9.0, "ma10": 10.0,   # 无金叉
             "ma5_prev": 8.9, "ma10_prev": 10.0, "vol_ratio": 1.5, "bias_ma5": 0.01}]
    out = st.STRATEGY_SCORERS["ma_golden_cross"](_panel(rows))
    assert list(out["code"]) == ["000001"]
    assert out.iloc[0]["signal_score"] > 0


def test_volume_breakout_hits():
    rows = [{"code": "000001", "name": "A", "close": 11.0, "high_20": 10.9, "vol_ratio": 2.5,
             "bias_ma5": 0.02, "ma5": 10.8},
            {"code": "000002", "name": "B", "close": 10.0, "high_20": 10.9, "vol_ratio": 2.5,  # 未破高
             "bias_ma5": 0.02, "ma5": 10.8}]
    out = st.STRATEGY_SCORERS["volume_breakout"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_bottom_volume_hits():
    rows = [{"code": "000001", "name": "A", "close": 8.5, "open_": 8.3, "high_20": 10.0,  # 跌幅>15%
             "low_30": 8.4, "vol_ratio": 3.5, "ret_from_high20": -0.16},
            {"code": "000002", "name": "B", "close": 9.9, "open_": 9.8, "high_20": 10.0,   # 跌幅不足
             "low_30": 9.7, "vol_ratio": 3.5, "ret_from_high20": -0.01}]
    out = st.STRATEGY_SCORERS["bottom_volume"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_shrink_pullback_hits():
    rows = [{"code": "000001", "name": "A", "close": 10.05, "ma5": 10.0, "ma10": 9.8, "ma20": 9.6,
             "vol_ratio": 0.6, "bias_ma5": 0.005},
            {"code": "000002", "name": "B", "close": 10.05, "ma5": 10.0, "ma10": 9.8, "ma20": 9.6,
             "vol_ratio": 1.5, "bias_ma5": 0.005}]  # 未缩量
    out = st.STRATEGY_SCORERS["shrink_pullback"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_bull_trend_hits():
    rows = [{"code": "000001", "name": "A", "close": 10.5, "ma5": 10.3, "ma10": 10.1, "ma20": 10.0, "bias_ma5": 0.02},
            {"code": "000002", "name": "B", "close": 9.0, "ma5": 9.5, "ma10": 10.1, "ma20": 10.0, "bias_ma5": -0.05}]
    out = st.STRATEGY_SCORERS["bull_trend"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def _hist(rows):  # rows: list of dict(open,high,low,close,vol)
    return pd.DataFrame(rows)


def test_one_yang_three_yin_hits():
    # 第1日大阳(实体>2%)，中间3日小阴不破首日开盘，第5日阳线破首日收盘
    h = _hist([
        {"open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4, "vol": 2000},  # 大阳
        {"open": 10.3, "high": 10.4, "low": 10.1, "close": 10.2, "vol": 1200},
        {"open": 10.2, "high": 10.3, "low": 10.05, "close": 10.15, "vol": 1000},
        {"open": 10.15, "high": 10.25, "low": 10.02, "close": 10.1, "vol": 900},
        {"open": 10.2, "high": 10.6, "low": 10.15, "close": 10.5, "vol": 1800},  # 阳线破首日收盘
    ])
    rows = [{"code": "000001", "name": "A", "ma5": 10.3, "ma10": 10.1, "ma20": 10.0}]
    panel = _panel(rows, history={"000001": h})
    out = st.STRATEGY_SCORERS["one_yang_three_yin"](panel)
    assert list(out["code"]) == ["000001"]


def test_box_oscillation_hits_at_bottom():
    # 箱体：60日在 9.5~10.5 区间，现价贴近箱底（距支撑<=5%）
    closes = [10.0, 10.4, 9.6, 10.3, 9.55, 10.45, 9.6, 10.4, 9.7] * 7
    h = _hist([{"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "vol": 1000} for c in closes[:60]])
    h.loc[len(h) - 1, ["open", "high", "low", "close"]] = [9.6, 9.65, 9.5, 9.6]  # 现价近箱底
    rows = [{"code": "000001", "name": "A", "close": 9.6}]
    panel = _panel(rows, history={"000001": h})
    out = st.STRATEGY_SCORERS["box_oscillation"](panel)
    assert list(out["code"]) == ["000001"]


def test_growth_quality_filters_by_valuation():
    rows = [{"code": "000001", "name": "A", "pe": 25.0, "pb": 3.0, "total_mv": 8e6},  # 合理
            {"code": "000002", "name": "B", "pe": -5.0, "pb": 3.0, "total_mv": 8e6},  # 亏损剔除
            {"code": "000003", "name": "C", "pe": 300.0, "pb": 3.0, "total_mv": 8e6},  # 过高剔除
            {"code": "000004", "name": "D", "pe": 25.0, "pb": 3.0, "total_mv": 2e5}]  # 市值过小剔除
    out = st.STRATEGY_SCORERS["growth_quality"](_panel(rows))
    assert list(out["code"]) == ["000001"]


def test_skill_loads_trading_style(tmp_path):
    from src.agent.skills.base import load_skill_from_yaml
    f = tmp_path / "s.yaml"
    f.write_text("name: x\ndisplay_name: X\ndescription: d\ninstructions: i\ntrading_style: 抄底、左侧反转\n", encoding="utf-8")
    skill = load_skill_from_yaml(f)
    assert skill.trading_style == "抄底、左侧反转"
