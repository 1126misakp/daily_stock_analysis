# -*- coding: utf-8 -*-
import pandas as pd
from src.services.stock_screener.market_data import build_panel_from_frames


def _daily(code, dates, closes, vols):
    pre = [closes[0]] + closes[:-1]
    return pd.DataFrame({
        "ts_code": code, "trade_date": dates,
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "pre_close": pre, "pct_chg": [(c / p - 1) * 100 for c, p in zip(closes, pre)],
        "vol": vols, "amount": [c * v for c, v in zip(closes, vols)],
    })


def test_build_panel_computes_features():
    dates = ["20260520", "20260521", "20260522", "20260523", "20260526",
             "20260527", "20260528", "20260529", "20260530", "20260602"]
    closes = [10, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 11.0]
    vols = [1000] * 9 + [3000]
    daily = pd.concat([_daily("000001.SZ", dates, closes, vols)], ignore_index=True)
    basic = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260602"],
                          "pe": [15.0], "pb": [1.5], "total_mv": [5e6],
                          "turnover_rate": [2.0], "volume_ratio": [3.0]})
    names = {"000001": "平安银行"}
    industry = {"000001": "银行"}
    panel = build_panel_from_frames(daily, basic, names, industry, trade_date="20260602")
    row = panel.latest.loc["000001"]
    assert row["close"] == 11.0
    assert round(row["ma5"], 2) == round((10.6 + 10.7 + 10.8 + 11.0 + 10.5) / 5, 2)
    assert row["vol_ratio"] > 2.5         # 当日 3000 / 5日均量
    assert round(row["change_pct"], 4) == round(11.0 / 10.8 - 1, 4)  # 用 pct_chg
    assert panel.names["000001"] == "平安银行"


def test_build_panel_amount_converts_thousand_yuan_to_yuan():
    # Tushare daily 的 amount 单位为千元，build_panel 须换算为元（×1000）
    dates = ["20260530", "20260602"]
    closes = [10.0, 10.0]
    vols = [1000, 1000]
    raw = _daily("000001.SZ", dates, closes, vols)
    last_amount_thousand = float(raw.iloc[-1]["amount"])  # 原始千元口径
    daily = pd.concat([raw], ignore_index=True)
    basic = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20260602"],
                          "pe": [15.0], "pb": [1.5], "total_mv": [5e6],
                          "turnover_rate": [2.0], "volume_ratio": [1.0]})
    panel = build_panel_from_frames(daily, basic, {"000001": "平安银行"},
                                    {"000001": "银行"}, trade_date="20260602")
    assert panel.latest.loc["000001"]["amount"] == last_amount_thousand * 1000


def test_fetch_market_panel_uses_tushare_batch(monkeypatch):
    from src.services.stock_screener import market_data as md
    calls = {"daily": 0}

    class FakeTushare:
        def _fundamental_df(self, api, **kw):
            if api == "trade_cal":
                return pd.DataFrame({"cal_date": ["20260530", "20260602"], "is_open": [1, 1]})
            if api == "daily":
                calls["daily"] += 1
                d = kw["trade_date"]
                return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [d],
                    "open": [10], "high": [10.1], "low": [9.9], "close": [10], "vol": [1000], "amount": [10000]})
            if api == "daily_basic":
                return pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": [kw["trade_date"]],
                    "pe": [15], "pb": [1.5], "total_mv": [5e6], "turnover_rate": [2], "volume_ratio": [1]})
            return None

        def get_stock_list(self):
            return pd.DataFrame({"code": ["000001"], "name": ["平安银行"], "industry": ["银行"]})

    monkeypatch.setattr(md, "_get_tushare", lambda: FakeTushare())
    panel = md.fetch_market_panel(n_days=2)
    assert panel.universe_size == 1
    assert calls["daily"] == 2            # 每个交易日一次全市场调用
    assert panel.industry["000001"] == "银行"
