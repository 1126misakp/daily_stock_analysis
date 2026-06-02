# 自研选股引擎 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除全挂的 AlphaSift 选股渠道，用本项目 `data_provider`（Tushare 全市场批量接口）+ 现有 LLM 封装，实现"按策略 + 用户偏好"的两段式自研选股引擎。

**Architecture:** 第1段在全市场（Tushare `daily`/`daily_basic` 按交易日批量取数）上用 pandas 向量化跑策略量化信号，足切到候选池（≤80）；第2段把候选池摘要交给现有 LiteLLM 通道（自动走 MiMo）做一次轻量重排，输出排序+理由+风险。异步 job 复用现有内存 job store。不触碰数据源优先铁律。

**Tech Stack:** Python 3 / FastAPI / pandas / LiteLLM / pytest（后端）；React + TypeScript + Vitest（前端）。

> **仓库规范（AGENTS.md，务必遵守）：**
> - commit message 用**英文**，**不加** `Co-Authored-By`。
> - 未经确认不 `git push`/`git tag`（本计划只在本地分支 commit）。
> - 新增配置项同步 `.env.example` 与文档；用户可见能力/API 变化同步 `docs/` 与 `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式 `- [类型] 描述`）。
> - 不写死密钥/路径/模型名/端口。
> - 后端改动跑 `./scripts/ci_gate.sh`；前端改动跑 `cd apps/dsa-web && npm ci && npm run lint && npm run build`。

---

## 文件结构

**新增（后端）：**
- `src/services/stock_screener/__init__.py` — 包导出 `run_screen`
- `src/services/stock_screener/market_data.py` — `MarketPanel` 数据结构 + `fetch_market_panel()` 全市场取数
- `src/services/stock_screener/strategies.py` — 8 个策略的量化 scorer + 注册表
- `src/services/stock_screener/ranker.py` — 第2段 LLM 轻量重排（含降级）
- `src/services/stock_screener/engine.py` — `run_screen()` 编排：校验输入组合 → 第1段 → 偏好板块过滤 → 第2段
- `src/services/screen_jobs.py` — 由 `alphasift_screen_jobs.py` 泛化改名而来
- `api/v1/endpoints/screen.py` — 新选股端点

**新增（测试）：**
- `tests/test_screen_strategies.py`、`tests/test_screen_market_data.py`、`tests/test_screen_ranker.py`、`tests/test_screen_engine.py`、`tests/test_screen_jobs.py`、`tests/test_screen_api.py`

**新增（前端）：**
- `apps/dsa-web/src/api/screen.ts` — 由 `alphasift.ts` 泛化改名

**修改：**
- `src/agent/skills/base.py` — `Skill` 加 `trading_style` 字段 + YAML/MD 加载器解析
- `strategies/*.yaml`（8 个上线策略）— 各加 `trading_style` 字段
- `api/v1/router` 注册（移除 alphasift 路由，挂 screen 路由）
- `apps/dsa-web/src/pages/StockScreeningPage.tsx` — 偏好输入框、策略卡片操盘风格、校验、改 import
- `.env.example`、`docs/CHANGELOG.md`、相关 docs、`CLAUDE.md`(外层维护说明)

**删除：**
- `api/v1/endpoints/alphasift.py`、`src/services/alphasift_screen_jobs.py`、`apps/dsa-web/src/api/alphasift.ts`、`tests/test_alphasift_api.py`

---

## 数据契约（贯穿全计划，先读）

### MarketPanel（`market_data.py`）
```python
@dataclass
class MarketPanel:
    trade_date: str                      # 最新交易日 YYYYMMDD
    latest: pd.DataFrame                 # 每股一行的特征表，index=code
    history: Dict[str, pd.DataFrame]     # code -> 按日期升序的日K（open/high/low/close/vol/amount）
    basic: pd.DataFrame                  # 最新交易日 daily_basic，index=code（pe/pb/total_mv/turnover_rate/volume_ratio）
    names: Dict[str, str]                # code -> 股票名
    industry: Dict[str, str]             # code -> 所属行业（来自 stock_basic）
```

`latest` 表列（最新交易日 T，所有股票一行）：
`code, name, close, open_, high, low, vol, amount, change_pct, ma5, ma10, ma20, ma5_prev, ma10_prev, vol_ma5, vol_ratio, high_20, low_30, ret_from_high20, bias_ma5, pe, pb, total_mv, turnover_rate, industry`

> **实测要点（2026-06-02 生产 token 验证）**：Tushare `daily` 按 `trade_date` 一次返回**全市场 5507 行**（不截断），列含 `pct_chg`（百分比涨跌幅，如 1.5 表示 +1.5%）和 `pre_close`，因此 `change_pct` 直接取自 daily 的 `pct_chg/100`，**无需自算 prev_close**。`daily_basic` 同 5507 行含 `pe/pb/total_mv(万元)/turnover_rate/volume_ratio`。60 天串行取数 ≈76s（异步 job 可接受，47 次/分钟 < 80 限频上限，不触发整分钟 sleep）。

### scorer 输出（每个策略统一）
`pd.DataFrame`，列：`code, name, signal_score(float), signal_detail(str)`，只含命中该策略的股票。

### 候选 dict（喂第2段 / 返回前端）
```python
{"code","name","signal_score","signal_detail","close","change_pct","amount","pe","pb","industry"}
```

### run_screen 返回（对齐前端 AlphaSiftScreenResponse 形状）
```python
{
  "enabled": True, "candidates": [...], "candidateCount": int,
  "run_id": str, "strategy": str|None, "preference": str|None,
  "snapshot_count": int, "after_filter_count": int,
  "llm_ranked": bool, "llm_selection_logic": str, "llm_portfolio_risk": str,
  "warnings": [str], "source_errors": [str],
}
```
candidate dict 字段（后端 snake_case，前端 `ScreenCandidate`(Task 10) 经 `toCamelCase` 转换）：
`rank, code, name, score, screen_score(=signal_score), reason, llm_thesis, llm_risks, llm_style_fit, price, change_pct, amount, industry, raw`

---

## 全局执行约定（每个 Task 都适用，吸收自 prd-reviewer）

- **测试离线纪律**：所有 `tests/test_screen_*.py` 禁止真实联网。凡会触发 `fetch_market_panel`/`run_screen` 的用例必须 monkeypatch 到桩。`test_screen_api.py` 因端点用 `from src.services.stock_screener import run_screen`，须 patch **端点模块属性** `ep.run_screen`（不是 engine 模块）。
- **单例重置**：每个用到 `ScreenJobStore` 的用例开头 `ScreenJobStore._instance = None`（单例跨用例串扰 + 幂等复用进行中 job 会让第二个用例复用第一个的 job）。
- **字段唯一真源**：`latest` 表列清单（含 `change_pct`）以"数据契约"段为准；任何 scorer/engine/ranker 引用的列必须先确认 `build_panel_from_frames` 已计算，否则补计算。
- **路由 prefix 约定**：端点文件内 `APIRouter()` 不写 prefix，prefix 一律在 `api/v1/router.py` 的 `include_router(..., prefix="/screen")` 处给（与现有所有端点一致）。
- **端点范式**：新端点用 `async def`（FastAPI 原生），测试用 `asyncio.run(ep.func(...))` 直接调用协程，不用 TestClient。注意与现有 `test_alphasift_api.py`（同步端点、同步调用）范式不同，这是**有意为之**。
- **AlphaSift 残留删除顺序**：先改所有 import 引用（后端 `endpoints/__init__.py`/`router.py`、前端各文件与测试 mock），**最后**再删源文件，避免中途 import 崩溃导致 ci_gate/build 红。
- **卡壳即停**：若真实 Tushare 接口行为（积分/行数/限频）与计划假设不符，停下来在交付说明里报告，不要自行降级 N 或改数据源（数据源铁律）。

---

## Phase 1：全市场取数层

### Task 1: MarketPanel 取数 `fetch_market_panel`

**Files:**
- Create: `src/services/stock_screener/__init__.py`
- Create: `src/services/stock_screener/market_data.py`
- Test: `tests/test_screen_market_data.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_screen_market_data.py
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
    dates = ["20260520","20260521","20260522","20260523","20260526",
             "20260527","20260528","20260529","20260530","20260602"]
    closes = [10,10.1,10.2,10.3,10.4,10.5,10.6,10.7,10.8,11.0]
    vols = [1000]*9 + [3000]
    daily = pd.concat([_daily("000001.SZ", dates, closes, vols)], ignore_index=True)
    basic = pd.DataFrame({"ts_code":["000001.SZ"],"trade_date":["20260602"],
                          "pe":[15.0],"pb":[1.5],"total_mv":[5e6],
                          "turnover_rate":[2.0],"volume_ratio":[3.0]})
    names = {"000001": "平安银行"}; industry = {"000001": "银行"}
    panel = build_panel_from_frames(daily, basic, names, industry, trade_date="20260602")
    row = panel.latest.loc["000001"]
    assert row["close"] == 11.0
    assert round(row["ma5"], 2) == round((10.6+10.7+10.8+11.0+10.5)/5, 2)
    assert row["vol_ratio"] > 2.5         # 当日 3000 / 5日均量
    assert round(row["change_pct"], 4) == round(11.0 / 10.8 - 1, 4)  # 用 pct_chg
    assert panel.names["000001"] == "平安银行"
```

- [ ] **Step 2: 跑测试确认失败** — `python -m pytest tests/test_screen_market_data.py -v`，预期 ImportError。

- [ ] **Step 3: 实现 `market_data.py`**

```python
# -*- coding: utf-8 -*-
"""自研选股：全市场行情快照取数与特征预计算。

仅在选股流程内通过 Tushare 全市场批量接口（按交易日）取数，属独立调用路径，
不修改 data_provider 的路由/优先级/fetcher 链（数据源优先铁律不受影响）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _bare_code(ts_code: str) -> str:
    return str(ts_code).split(".")[0]


@dataclass
class MarketPanel:
    trade_date: str
    latest: pd.DataFrame
    history: Dict[str, pd.DataFrame]
    basic: pd.DataFrame
    names: Dict[str, str] = field(default_factory=dict)
    industry: Dict[str, str] = field(default_factory=dict)

    @property
    def universe_size(self) -> int:
        return int(len(self.latest))


def build_panel_from_frames(daily, basic, names, industry, trade_date) -> MarketPanel:
    """从已取好的全市场多日 daily + 最新 daily_basic 构造 MarketPanel。"""
    daily = daily.copy()
    daily["code"] = daily["ts_code"].map(_bare_code)
    daily = daily.sort_values(["code", "trade_date"])

    history: Dict[str, pd.DataFrame] = {}
    rows: List[dict] = []
    for code, grp in daily.groupby("code"):
        g = grp.reset_index(drop=True)
        history[code] = g
        closes = g["close"].astype(float)
        vols = g["vol"].astype(float)
        last = g.iloc[-1]
        ma5 = closes.tail(5).mean()
        ma10 = closes.tail(10).mean()
        ma20 = closes.tail(20).mean()
        ma5_prev = closes.iloc[-6:-1].mean() if len(closes) >= 6 else ma5
        ma10_prev = closes.iloc[-11:-1].mean() if len(closes) >= 11 else ma10
        vol_ma5 = vols.tail(5).mean()
        vol_ratio = float(last["vol"]) / vol_ma5 if vol_ma5 else 0.0
        high_20 = g["high"].tail(20).astype(float).max()
        low_30 = g["low"].tail(30).astype(float).min()
        # change_pct：优先用 Tushare daily 自带 pct_chg（百分比→小数），否则用收盘价回推
        if "pct_chg" in g.columns and pd.notna(last.get("pct_chg")):
            change_pct = float(last["pct_chg"]) / 100.0
        elif len(closes) >= 2:
            change_pct = float(closes.iloc[-1]) / float(closes.iloc[-2]) - 1.0
        else:
            change_pct = 0.0
        rows.append({
            "code": code, "name": names.get(code, code),
            "close": float(last["close"]), "open_": float(last["open"]),
            "high": float(last["high"]), "low": float(last["low"]),
            "vol": float(last["vol"]), "amount": float(last["amount"]),
            "change_pct": change_pct,
            "ma5": ma5, "ma10": ma10, "ma20": ma20,
            "ma5_prev": ma5_prev, "ma10_prev": ma10_prev,
            "vol_ma5": vol_ma5, "vol_ratio": vol_ratio,
            "high_20": high_20, "low_30": low_30,
            "ret_from_high20": (float(last["close"]) / high_20 - 1) if high_20 else 0.0,
            "bias_ma5": (float(last["close"]) / ma5 - 1) if ma5 else 0.0,
            "industry": industry.get(code, ""),
        })
    latest = pd.DataFrame(rows).set_index("code")

    b = basic.copy()
    if "ts_code" in b.columns:
        b["code"] = b["ts_code"].map(_bare_code)
        b = b.set_index("code")
    for col in ("pe", "pb", "total_mv", "turnover_rate", "volume_ratio"):
        if col in b.columns:
            latest[col] = b[col]
        else:
            latest[col] = pd.NA
    return MarketPanel(trade_date=trade_date, latest=latest, history=history,
                       basic=b, names=names, industry=industry)
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_market_data.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/__init__.py src/services/stock_screener/market_data.py tests/test_screen_market_data.py
git commit -m "feat: add MarketPanel and feature pre-computation for screener"
```

### Task 2: `fetch_market_panel` 真实取数（Tushare 批量）

**Files:**
- Modify: `src/services/stock_screener/market_data.py`
- Test: `tests/test_screen_market_data.py`

- [ ] **Step 1: 写失败测试（mock TushareFetcher）**

```python
def test_fetch_market_panel_uses_tushare_batch(monkeypatch):
    from src.services.stock_screener import market_data as md
    calls = {"daily": 0}
    class FakeTushare:
        def _fundamental_df(self, api, **kw):
            if api == "trade_cal":
                return pd.DataFrame({"cal_date":["20260530","20260602"],"is_open":[1,1]})
            if api == "daily":
                calls["daily"] += 1
                d = kw["trade_date"]
                return pd.DataFrame({"ts_code":["000001.SZ"],"trade_date":[d],
                    "open":[10],"high":[10.1],"low":[9.9],"close":[10],"vol":[1000],"amount":[10000]})
            if api == "daily_basic":
                return pd.DataFrame({"ts_code":["000001.SZ"],"trade_date":[kw["trade_date"]],
                    "pe":[15],"pb":[1.5],"total_mv":[5e6],"turnover_rate":[2],"volume_ratio":[1]})
            return None
        def get_stock_list(self):
            return pd.DataFrame({"code":["000001"],"name":["平安银行"],"industry":["银行"]})
    monkeypatch.setattr(md, "_get_tushare", lambda: FakeTushare())
    panel = md.fetch_market_panel(n_days=2)
    assert panel.universe_size == 1
    assert calls["daily"] == 2            # 每个交易日一次全市场调用
    assert panel.industry["000001"] == "银行"
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError（无 `fetch_market_panel`）。

- [ ] **Step 3: 追加实现**

```python
def _get_tushare():
    """复用 history_loader 的共享 fetcher manager，拿 TushareFetcher（共享限频）。"""
    from src.services.history_loader import _get_fetcher_manager
    manager = _get_fetcher_manager()
    fetcher = manager._get_fetcher_by_name("TushareFetcher")
    if fetcher is None:
        raise RuntimeError("Tushare 未配置，无法执行全市场选股取数")
    return fetcher


def _recent_trade_dates(tushare, n_days: int, end_date: Optional[str] = None) -> List[str]:
    end = end_date or datetime.now().strftime("%Y%m%d")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=n_days * 2 + 30)).strftime("%Y%m%d")
    cal = tushare._fundamental_df("trade_cal", exchange="SSE", start_date=start, end_date=end)
    if cal is None or cal.empty:
        raise RuntimeError("无法获取交易日历")
    opens = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
    return opens[-n_days:]


def fetch_market_panel(n_days: int = 60, end_date: Optional[str] = None) -> MarketPanel:
    """全市场近 n_days 日行情快照。约 n_days 次 daily + 1 次 daily_basic + 1 次 stock_basic。"""
    tushare = _get_tushare()
    dates = _recent_trade_dates(tushare, n_days, end_date)
    if not dates:
        raise RuntimeError("无可用交易日")
    frames = []
    EXPECTED_MIN_ROWS = 4000   # A股全市场实测约 5507 行，明显偏少视为截断/限权
    for d in dates:
        df = tushare._fundamental_df("daily", trade_date=d)
        if df is not None and not df.empty:
            if len(df) < EXPECTED_MIN_ROWS:
                logger.warning("[选股取数] %s daily 仅 %d 行，疑似被截断或积分受限", d, len(df))
            frames.append(df)
    if not frames:
        raise RuntimeError("Tushare daily 全市场取数为空")
    daily = pd.concat(frames, ignore_index=True)
    latest_date = dates[-1]
    basic = tushare._fundamental_df("daily_basic", trade_date=latest_date)
    if basic is None:
        basic = pd.DataFrame()
    stock_list = tushare.get_stock_list()
    names, industry = {}, {}
    if stock_list is not None and not stock_list.empty:
        code_col = "code" if "code" in stock_list.columns else "ts_code"
        for _, r in stock_list.iterrows():
            c = _bare_code(r[code_col])
            names[c] = str(r.get("name", c))
            industry[c] = str(r.get("industry", "") or "")
    return build_panel_from_frames(daily, basic, names, industry, trade_date=latest_date)
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_market_data.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/market_data.py tests/test_screen_market_data.py
git commit -m "feat: fetch full-market panel via Tushare batch APIs"
```

---

## Phase 2：策略 scorer

> 每个 scorer：输入 `MarketPanel`，输出命中股票的 `DataFrame[code,name,signal_score,signal_detail]`。
> 量化判据严格取自对应 `strategies/<id>.yaml` 的 `instructions`。
> 注册表 `STRATEGY_SCORERS: Dict[str, Callable[[MarketPanel], pd.DataFrame]]`。

### Task 3: 注册表骨架 + 趋势/反转类 scorer（向量化）

**Files:**
- Create: `src/services/stock_screener/strategies.py`
- Test: `tests/test_screen_strategies.py`

涵盖 5 个可向量化策略：`ma_golden_cross / volume_breakout / bottom_volume / shrink_pullback / bull_trend`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_screen_strategies.py
import pandas as pd
from src.services.stock_screener.market_data import MarketPanel
from src.services.stock_screener import strategies as st

def _panel(latest_rows, history=None):
    latest = pd.DataFrame(latest_rows).set_index("code")
    return MarketPanel(trade_date="20260602", latest=latest,
                       history=history or {}, basic=pd.DataFrame(),
                       names={r["code"]: r["name"] for r in latest_rows},
                       industry={r["code"]: r.get("industry","") for r in latest_rows})

def test_ma_golden_cross_hits():
    # 命中：今日 ma5>ma10 且昨日 ma5<=ma10（金叉），量比>1.2，乖离<5%
    rows = [{"code":"000001","name":"A","close":10.2,"ma5":10.1,"ma10":10.0,
             "ma5_prev":9.9,"ma10_prev":10.0,"vol_ratio":1.5,"bias_ma5":0.01},
            {"code":"000002","name":"B","close":10.2,"ma5":9.0,"ma10":10.0,   # 无金叉
             "ma5_prev":8.9,"ma10_prev":10.0,"vol_ratio":1.5,"bias_ma5":0.01}]
    out = st.STRATEGY_SCORERS["ma_golden_cross"](_panel(rows))
    assert list(out["code"]) == ["000001"]
    assert out.iloc[0]["signal_score"] > 0

def test_volume_breakout_hits():
    rows = [{"code":"000001","name":"A","close":11.0,"high_20":10.9,"vol_ratio":2.5,
             "bias_ma5":0.02,"ma5":10.8},
            {"code":"000002","name":"B","close":10.0,"high_20":10.9,"vol_ratio":2.5,  # 未破高
             "bias_ma5":0.02,"ma5":10.8}]
    out = st.STRATEGY_SCORERS["volume_breakout"](_panel(rows))
    assert list(out["code"]) == ["000001"]

def test_bottom_volume_hits():
    rows = [{"code":"000001","name":"A","close":8.5,"open_":8.3,"high_20":10.0,  # 跌幅>15%
             "low_30":8.4,"vol_ratio":3.5,"ret_from_high20":-0.16},
            {"code":"000002","name":"B","close":9.9,"open_":9.8,"high_20":10.0,   # 跌幅不足
             "low_30":9.7,"vol_ratio":3.5,"ret_from_high20":-0.01}]
    out = st.STRATEGY_SCORERS["bottom_volume"](_panel(rows))
    assert list(out["code"]) == ["000001"]

def test_shrink_pullback_hits():
    rows = [{"code":"000001","name":"A","close":10.05,"ma5":10.0,"ma10":9.8,"ma20":9.6,
             "vol_ratio":0.6,"bias_ma5":0.005},
            {"code":"000002","name":"B","close":10.05,"ma5":10.0,"ma10":9.8,"ma20":9.6,
             "vol_ratio":1.5,"bias_ma5":0.005}]  # 未缩量
    out = st.STRATEGY_SCORERS["shrink_pullback"](_panel(rows))
    assert list(out["code"]) == ["000001"]

def test_bull_trend_hits():
    rows = [{"code":"000001","name":"A","close":10.5,"ma5":10.3,"ma10":10.1,"ma20":10.0,"bias_ma5":0.02},
            {"code":"000002","name":"B","close":9.0,"ma5":9.5,"ma10":10.1,"ma20":10.0,"bias_ma5":-0.05}]
    out = st.STRATEGY_SCORERS["bull_trend"](_panel(rows))
    assert list(out["code"]) == ["000001"]
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError。

- [ ] **Step 3: 实现 `strategies.py`（注册表 + 5 个向量化 scorer）**

```python
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
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_strategies.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/strategies.py tests/test_screen_strategies.py
git commit -m "feat: add vectorized scorers for 5 trend/reversal strategies"
```

### Task 4: 形态/框架/基本面类 scorer

**Files:**
- Modify: `src/services/stock_screener/strategies.py`
- Test: `tests/test_screen_strategies.py`

涵盖 3 个：`one_yang_three_yin`（形态，用 history 序列）、`box_oscillation`（箱体，用 history）、`growth_quality`（基本面，用 daily_basic 粗筛，成长细节留给第2段 LLM）。

- [ ] **Step 1: 写失败测试**

```python
def _hist(rows):  # rows: list of dict(open,high,low,close,vol)
    return pd.DataFrame(rows)

def test_one_yang_three_yin_hits():
    # 第1日大阳(实体>2%)，中间3日小阴不破首日开盘，第5日阳线破首日收盘
    h = _hist([
        {"open":10.0,"high":10.5,"low":9.9,"close":10.4,"vol":2000},  # 大阳
        {"open":10.3,"high":10.4,"low":10.1,"close":10.2,"vol":1200},
        {"open":10.2,"high":10.3,"low":10.05,"close":10.15,"vol":1000},
        {"open":10.15,"high":10.25,"low":10.02,"close":10.1,"vol":900},
        {"open":10.2,"high":10.6,"low":10.15,"close":10.5,"vol":1800},  # 阳线破首日收盘
    ])
    rows = [{"code":"000001","name":"A","ma5":10.3,"ma10":10.1,"ma20":10.0}]
    panel = _panel(rows, history={"000001": h})
    out = st.STRATEGY_SCORERS["one_yang_three_yin"](panel)
    assert list(out["code"]) == ["000001"]

def test_box_oscillation_hits_at_bottom():
    # 箱体：60日在 9.5~10.5 区间，现价贴近箱底（距支撑<=5%）
    closes = [10.0,10.4,9.6,10.3,9.55,10.45,9.6,10.4,9.7]*7
    h = _hist([{"open":c,"high":c*1.01,"low":c*0.99,"close":c,"vol":1000} for c in closes[:60]])
    h.loc[len(h)-1, ["open","high","low","close"]] = [9.6, 9.65, 9.5, 9.6]  # 现价近箱底
    rows = [{"code":"000001","name":"A","close":9.6}]
    panel = _panel(rows, history={"000001": h})
    out = st.STRATEGY_SCORERS["box_oscillation"](panel)
    assert list(out["code"]) == ["000001"]

def test_growth_quality_filters_by_valuation():
    rows = [{"code":"000001","name":"A","pe":25.0,"pb":3.0,"total_mv":8e6},  # 合理
            {"code":"000002","name":"B","pe":-5.0,"pb":3.0,"total_mv":8e6},  # 亏损剔除
            {"code":"000003","name":"C","pe":300.0,"pb":3.0,"total_mv":8e6}, # 过高剔除
            {"code":"000004","name":"D","pe":25.0,"pb":3.0,"total_mv":2e5}]  # 市值过小剔除
    out = st.STRATEGY_SCORERS["growth_quality"](_panel(rows))
    assert list(out["code"]) == ["000001"]
```

- [ ] **Step 2: 跑测试确认失败** — 预期 KeyError（注册表无对应 key）。

- [ ] **Step 3: 追加实现**

```python
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
        near_bottom = (price - bottom) / bottom <= 0.05
        if near_bottom:
            name = panel.names.get(code, code)
            hits.append({"code": code, "name": name, "signal_score": 10.0,
                         "signal_detail": f"箱体震荡：现价贴近箱底（{bottom:.2f}~{top:.2f}）"})
    return pd.DataFrame(hits, columns=["code", "name", "signal_score", "signal_detail"])


def growth_quality(panel: MarketPanel) -> pd.DataFrame:
    """第1段仅按估值/市值粗筛出'值得 LLM 深看的成长候选'，成长性判断交第2段 LLM。"""
    df = panel.latest
    pe = pd.to_numeric(df.get("pe"), errors="coerce")
    total_mv = pd.to_numeric(df.get("total_mv"), errors="coerce")  # 单位：万元
    cond = (pe > 0) & (pe < 80) & (total_mv > 3e5)   # 盈利、估值不过热、市值>30亿
    score = cond.astype(float) * (15 - (pe.clip(upper=80) / 80) * 5)
    return _emit(df, score.where(cond, 0), "成长质量：盈利且估值合理，待LLM核成长")
```

并在 `STRATEGY_SCORERS` 注册表追加这三项：
```python
STRATEGY_SCORERS.update({
    "one_yang_three_yin": one_yang_three_yin,
    "box_oscillation": box_oscillation,
    "growth_quality": growth_quality,
})
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_strategies.py -v`，预期全部 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/strategies.py tests/test_screen_strategies.py
git commit -m "feat: add pattern/box/growth scorers; 8 strategies complete"
```

---

## Phase 3：策略元信息（trading_style 字段）

### Task 5: Skill 加 `trading_style` 字段 + YAML 解析

**Files:**
- Modify: `src/agent/skills/base.py`（`Skill` dataclass + `load_skill_from_yaml` + `load_skill_from_markdown`）
- Test: `tests/test_screen_strategies.py`（追加）

- [ ] **Step 1: 写失败测试**
```python
def test_skill_loads_trading_style(tmp_path):
    from src.agent.skills.base import load_skill_from_yaml
    f = tmp_path / "s.yaml"
    f.write_text("name: x\ndisplay_name: X\ndescription: d\ninstructions: i\ntrading_style: 抄底、左侧反转\n", encoding="utf-8")
    skill = load_skill_from_yaml(f)
    assert skill.trading_style == "抄底、左侧反转"
```

- [ ] **Step 2: 跑测试确认失败** — 预期 AttributeError（Skill 无 trading_style）。

- [ ] **Step 3: 实现**

在 `Skill` dataclass 字段区（`preferred_model: str = ""` 之后）追加：
```python
    trading_style: str = ""
```

在 `load_skill_from_yaml` 的 `Skill(...)` 构造参数末尾追加：
```python
        trading_style=str(data.get("trading_style", "")).strip(),
```

在 `load_skill_from_markdown` 的 `Skill(...)` 构造里同样追加（从 frontmatter `data.get("trading_style","")`，缺省空串）。

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_strategies.py::test_skill_loads_trading_style -v`。

- [ ] **Step 5: 给 8 个上线策略 YAML 加 `trading_style`**

逐个编辑 `strategies/<id>.yaml`，在 `description:` 下一行加（值见下表）：

| 文件 | trading_style |
|------|------|
| ma_golden_cross.yaml | `trading_style: 趋势确认、稳健追涨` |
| volume_breakout.yaml | `trading_style: 追涨、激进` |
| bottom_volume.yaml | `trading_style: 抄底、左侧反转` |
| shrink_pullback.yaml | `trading_style: 回调低吸、稳健` |
| one_yang_three_yin.yaml | `trading_style: 趋势延续入场、中性` |
| growth_quality.yaml | `trading_style: 中长线价值、保守` |
| box_oscillation.yaml | `trading_style: 高抛低吸、稳健` |
| bull_trend.yaml | `trading_style: 趋势跟随、中性偏进取` |

- [ ] **Step 6: 跑后端 gate** — `python -m py_compile src/agent/skills/base.py` + 重新加载策略不报错。

- [ ] **Step 7: commit**
```bash
git add src/agent/skills/base.py strategies/ tests/test_screen_strategies.py
git commit -m "feat: add trading_style field to Skill and 8 screening strategies"
```

---

## Phase 4：第2段 LLM 轻量重排

### Task 6: ranker（含 LLM 失败降级）

**Files:**
- Create: `src/services/stock_screener/ranker.py`
- Test: `tests/test_screen_ranker.py`

设计：`rerank(candidates, strategy_desc, preference, max_results) -> dict`，返回 `{"candidates":[...], "llm_ranked":bool, "llm_selection_logic":str, "llm_portfolio_risk":str, "warnings":[...]}`。LLM 调用参考 `src/services/image_stock_extractor.py`：用 `config.litellm_model` + `config.llm_channels` 解析 model/base_url/key，调 `litellm.completion`。失败 → 按 signal_score 排序降级。

- [ ] **Step 1: 写失败测试**
```python
# tests/test_screen_ranker.py
from src.services.stock_screener import ranker

CANDS = [
    {"code":"000001","name":"A","signal_score":12,"signal_detail":"金叉","close":10,"change_pct":0.01,"industry":"科技"},
    {"code":"000002","name":"B","signal_score":8,"signal_detail":"金叉","close":20,"change_pct":-0.01,"industry":"医药"},
]

def test_rerank_llm_failure_falls_back_to_signal_score(monkeypatch):
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="", max_results=5)
    assert res["llm_ranked"] is False
    assert [c["code"] for c in res["candidates"]] == ["000001", "000002"]  # 按 signal_score 降序
    assert res["candidates"][0]["rank"] == 1
    assert any("LLM" in w for w in res["warnings"])

def test_rerank_uses_llm_order(monkeypatch):
    fake = '{"selection_logic":"偏好科技","portfolio_risk":"集中科技","ranking":[{"code":"000002","reason":"更稳","thesis":"t","risks":["r"],"style_fit":"贴合"}]}'
    monkeypatch.setattr(ranker, "_call_llm", lambda *a, **k: fake)
    res = ranker.rerank(CANDS, strategy_desc="金叉", preference="喜欢医药", max_results=5)
    assert res["llm_ranked"] is True
    assert res["candidates"][0]["code"] == "000002"
    assert res["candidates"][0]["reason"] == "更稳"
    assert res["llm_selection_logic"] == "偏好科技"
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError。

- [ ] **Step 3: 实现 `ranker.py`**
```python
# -*- coding: utf-8 -*-
"""自研选股第2段：候选池 LLM 轻量重排。复用现有 LiteLLM 通道（运行时走 MiMo）。"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import Dict, List, Optional

from src.config import get_config

logger = logging.getLogger(__name__)

_PROMPT = """你是A股选股助手。下面是经量化策略初筛出的候选股票。
请根据【策略】与【用户偏好】对候选排序并给出理由。规则：
- 用户偏好与策略冲突时，在候选范围内**优先满足用户偏好**（可少选、宁缺毋滥）。
- 只能从给定候选中选择，不得编造未列出的股票。
严格输出 JSON：{{"selection_logic":"一句话选股逻辑","portfolio_risk":"组合风险提示",
"ranking":[{{"code":"代码","reason":"一句话理由","thesis":"简要逻辑","risks":["风险1"],"style_fit":"与偏好/风格的契合度"}}]}}

【策略】{strategy}
【用户偏好】{preference}
【候选】
{table}
"""


def _fallback(candidates: List[dict], max_results: int, warning: str) -> dict:
    ranked = sorted(candidates, key=lambda c: c.get("signal_score", 0), reverse=True)[:max_results]
    for i, c in enumerate(ranked, 1):
        c["rank"] = i
        c.setdefault("reason", c.get("signal_detail", ""))
        c["score"] = c.get("signal_score")
    return {"candidates": ranked, "llm_ranked": False, "llm_selection_logic": "",
            "llm_portfolio_risk": "", "warnings": [warning]}


def _resolve_channel(cfg):
    """model 与 key/base_url/headers 取自**同一个**被选中的渠道，避免多渠道下错配。"""
    channel = next((c for c in (cfg.llm_channels or []) if c.get("api_keys")), None)
    if not channel:
        return None, None, None, None
    models = channel.get("models") or []
    model = models[0] if models else (cfg.litellm_model or "").strip()
    key = random.choice(channel["api_keys"]) if channel.get("api_keys") else None
    return model, key, channel.get("base_url"), channel.get("extra_headers")


def _call_llm(prompt: str) -> str:
    import litellm
    cfg = get_config()
    model, key, base_url, extra_headers = _resolve_channel(cfg)
    if not model or not key:
        raise RuntimeError("未配置可用 LLM 渠道")
    kwargs = {"model": model, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 2048, "api_key": key, "timeout": 90}
    if base_url:
        kwargs["api_base"] = base_url
    # 合并渠道自带 extra_headers；仅 aihubmix 渠道才补 APP-Code（MiMo 渠道 base_url 不含 aihubmix.com，不会误注入）
    headers = dict(extra_headers or {})
    if base_url and "aihubmix.com" in base_url:
        headers.setdefault("APP-Code", "GPIJ3886")
    if headers:
        kwargs["extra_headers"] = headers
    resp = litellm.completion(**kwargs)
    return resp.choices[0].message.content or ""


def _parse_json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _build_table(candidates: List[dict]) -> str:
    lines = []
    for c in candidates:
        lines.append(
            f"{c['code']} {c['name']} | 信号:{c.get('signal_detail','')} | "
            f"价:{c.get('close','-')} 涨跌:{c.get('change_pct','-')} "
            f"PE:{c.get('pe','-')} 行业:{c.get('industry','')}"
        )
    return "\n".join(lines)


def rerank(candidates: List[dict], strategy_desc: str, preference: str, max_results: int) -> dict:
    if not candidates:
        return {"candidates": [], "llm_ranked": False, "llm_selection_logic": "",
                "llm_portfolio_risk": "", "warnings": ["候选为空"]}
    prompt = _PROMPT.format(strategy=strategy_desc or "（未指定）",
                            preference=preference or "（无）",
                            table=_build_table(candidates))
    try:
        text = _call_llm(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("选股 LLM 重排失败，降级按量化打分排序: %s", exc)
        return _fallback(candidates, max_results, "LLM 重排不可用，已按量化打分排序")
    data = _parse_json(text)
    if not data or not isinstance(data.get("ranking"), list):
        return _fallback(candidates, max_results, "LLM 返回无法解析，已按量化打分排序")
    by_code = {c["code"]: c for c in candidates}
    ordered = []
    for i, item in enumerate(data["ranking"][:max_results], 1):
        base = by_code.get(str(item.get("code")))
        if not base:
            continue
        base = dict(base)
        base.update({"rank": i, "reason": item.get("reason", base.get("signal_detail", "")),
                     "llm_thesis": item.get("thesis", ""), "llm_risks": item.get("risks", []),
                     "llm_style_fit": item.get("style_fit", ""), "score": base.get("signal_score")})
        ordered.append(base)
    if not ordered:
        return _fallback(candidates, max_results, "LLM 未命中任何候选，已按量化打分排序")
    return {"candidates": ordered, "llm_ranked": True,
            "llm_selection_logic": data.get("selection_logic", ""),
            "llm_portfolio_risk": data.get("portfolio_risk", ""), "warnings": []}
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_ranker.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/ranker.py tests/test_screen_ranker.py
git commit -m "feat: add LLM lightweight re-rank with score fallback for screener"
```

---

## Phase 5：选股编排引擎

### Task 7: `run_screen` 编排 + 输入组合校验 + 偏好板块过滤

**Files:**
- Create: `src/services/stock_screener/engine.py`
- Modify: `src/services/stock_screener/__init__.py`（导出 `run_screen`、异常 `ScreenInputError`）
- Test: `tests/test_screen_engine.py`

行为表（spec §3.5）：
| 输入 | 行为 |
|------|------|
| 都空 | raise `ScreenInputError("策略和用户偏好至少填写一个")` |
| 仅偏好且无法识别板块/成分股 | raise `ScreenInputError("请补充板块/成分股偏好，或选择一个策略")` |
| 仅策略 | 策略 scorer 足切 → ranker |
| 策略+偏好 | 策略 scorer 足切 +（偏好板块则按 industry 过滤）→ ranker |
| 仅偏好（含板块） | 按 industry 过滤 + 基础流动性过滤，按量价粗排截断 150 → ranker |

- [ ] **Step 1: 写失败测试**
```python
# tests/test_screen_engine.py
import pandas as pd
import pytest
from src.services.stock_screener import engine
from src.services.stock_screener.market_data import MarketPanel

def _panel():
    latest = pd.DataFrame([
        {"code":"000001","name":"科技A","industry":"半导体","close":10,"amount":1e8,
         "ma5":10.1,"ma10":10.0,"ma5_prev":9.9,"ma10_prev":10.0,"vol_ratio":1.5,"bias_ma5":0.01,
         "change_pct":0.01,"pe":30,"pb":3,"total_mv":8e6,"high_20":9.9,"ma20":9.8,"ret_from_high20":0.01,"low_30":9},
        {"code":"000002","name":"银行B","industry":"银行","close":20,"amount":2e8,
         "ma5":19,"ma10":20,"ma5_prev":18.9,"ma10_prev":20,"vol_ratio":1.5,"bias_ma5":-0.05,
         "change_pct":-0.01,"pe":6,"pb":0.8,"total_mv":5e7,"high_20":21,"ma20":20.5,"ret_from_high20":-0.05,"low_30":19},
    ]).set_index("code")
    return MarketPanel(trade_date="20260602", latest=latest, history={}, basic=pd.DataFrame(),
                       names={"000001":"科技A","000002":"银行B"},
                       industry={"000001":"半导体","000002":"银行"})

def test_empty_inputs_rejected(monkeypatch):
    with pytest.raises(engine.ScreenInputError):
        engine.run_screen(strategy=None, preference="", max_results=20)

def test_preference_without_board_rejected(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "_extract_boards", lambda pref, industries: [])
    with pytest.raises(engine.ScreenInputError):
        engine.run_screen(strategy=None, preference="激进", max_results=20)

def test_strategy_only_runs(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "rerank",
        lambda cands, **k: {"candidates":cands,"llm_ranked":False,
                            "llm_selection_logic":"","llm_portfolio_risk":"","warnings":[]})
    res = engine.run_screen(strategy="ma_golden_cross", preference="", max_results=20)
    assert res["after_filter_count"] == 1
    assert res["candidates"][0]["code"] == "000001"

def test_strategy_plus_preference_does_not_hard_filter(monkeypatch):
    monkeypatch.setattr(engine, "fetch_market_panel", lambda **k: _panel())
    monkeypatch.setattr(engine, "_extract_boards", lambda pref, industries: ["银行"])
    monkeypatch.setattr(engine, "rerank", lambda cands, **k: {"candidates":cands,"llm_ranked":True,
        "llm_selection_logic":"","llm_portfolio_risk":"","warnings":[]})
    # 金叉命中 000001(半导体)，偏好限定银行：第1段不硬过滤，候选保留交 LLM 优先满足偏好
    res = engine.run_screen(strategy="ma_golden_cross", preference="只看银行", max_results=20)
    assert res["after_filter_count"] == 1
    assert any("偏好" in w for w in res["warnings"])
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError。

- [ ] **Step 3: 实现 `engine.py`**
```python
# -*- coding: utf-8 -*-
"""自研选股编排：输入校验 → 第1段策略足切 → 偏好板块过滤 → 第2段 LLM 重排。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import List, Optional

import pandas as pd

from .market_data import MarketPanel, fetch_market_panel
from .ranker import rerank
from .strategies import STRATEGY_SCORERS

logger = logging.getLogger(__name__)

SNAPSHOT_DAYS = 60
STRATEGY_POOL_CAP = 80
PREFERENCE_POOL_CAP = 150
MIN_AMOUNT = 1e7   # 基础流动性：成交额 > 1000 万


class ScreenInputError(ValueError):
    """策略与偏好都缺失，或仅偏好但无法识别板块。"""


def _strategy_meta(strategy_id: str):
    """从 SkillRegistry 读取策略 display_name/description/trading_style。"""
    try:
        from src.agent.skills.base import load_skills_from_directory, _BUILTIN_SKILLS_DIR
        for sk in load_skills_from_directory(_BUILTIN_SKILLS_DIR):
            if sk.name == strategy_id:
                return sk.display_name, sk.description, getattr(sk, "trading_style", "")
    except Exception:  # noqa: BLE001
        pass
    return strategy_id, "", ""


def _extract_boards(preference: str, industries: List[str]) -> List[str]:
    """从偏好自由文本里识别命中的行业板块（子串匹配）。"""
    if not preference:
        return []
    hit = []
    for ind in set(i for i in industries if i):
        if ind and ind in preference:
            hit.append(ind)
    return hit


def _to_candidate(panel: MarketPanel, code: str, signal_score: float, signal_detail: str) -> dict:
    row = panel.latest.loc[code]
    return {
        "code": code, "name": panel.names.get(code, code),
        "signal_score": float(signal_score), "signal_detail": signal_detail,
        "close": float(row.get("close")) if pd.notna(row.get("close")) else None,
        "change_pct": float(row.get("change_pct")) if "change_pct" in row and pd.notna(row.get("change_pct")) else None,
        "amount": float(row.get("amount")) if pd.notna(row.get("amount")) else None,
        "pe": float(row["pe"]) if "pe" in row and pd.notna(row["pe"]) else None,
        "pb": float(row["pb"]) if "pb" in row and pd.notna(row["pb"]) else None,
        "industry": panel.industry.get(code, ""),
    }


def run_screen(strategy: Optional[str], preference: Optional[str],
               max_results: int = 20, market: str = "cn") -> dict:
    strategy = (strategy or "").strip() or None
    preference = (preference or "").strip() or None
    if not strategy and not preference:
        raise ScreenInputError("策略和用户偏好至少填写一个")

    panel = fetch_market_panel(n_days=SNAPSHOT_DAYS)
    industries = list(panel.industry.values())
    boards = _extract_boards(preference, industries) if preference else []
    warnings: List[str] = []

    if strategy:
        scorer = STRATEGY_SCORERS.get(strategy)
        if scorer is None:
            raise ScreenInputError(f"未知策略：{strategy}")
        hit = scorer(panel)
        cands = [_to_candidate(panel, r["code"], r["signal_score"], r["signal_detail"])
                 for _, r in hit.iterrows()]
        # 策略+偏好：板块/风格不在第1段硬过滤（策略已决定候选来源），整体偏好交第2段 LLM
        # 优先满足，避免"策略命中行业与偏好板块不相交"时无故把候选清空。
        if preference and boards:
            warnings.append(f"已识别偏好板块 {boards}，将在 LLM 重排阶段优先满足你的偏好")
        cands.sort(key=lambda c: c["signal_score"], reverse=True)
        cands = cands[:STRATEGY_POOL_CAP]
    else:
        # 仅偏好：必须能识别板块
        if not boards:
            raise ScreenInputError("请补充板块/成分股偏好，或选择一个策略")
        df = panel.latest
        sub = df[df["industry"].isin(boards)]
        sub = sub[pd.to_numeric(sub["amount"], errors="coerce") > MIN_AMOUNT]
        sub = sub.sort_values("amount", ascending=False).head(PREFERENCE_POOL_CAP)
        cands = [_to_candidate(panel, code, 0.0, f"{panel.industry.get(code,'')}板块活跃标的")
                 for code in sub.index]

    after_filter_count = len(cands)
    disp_name, desc, _style = _strategy_meta(strategy) if strategy else ("", "", "")
    strategy_desc = f"{disp_name}：{desc}" if strategy else "（仅按用户偏好）"
    rr = rerank(cands, strategy_desc=strategy_desc, preference=preference or "", max_results=max_results)
    warnings.extend(rr.get("warnings", []))

    return {
        "enabled": True, "candidates": rr["candidates"], "candidateCount": len(rr["candidates"]),
        "run_id": datetime.now().strftime("%Y%m%d-") + uuid.uuid4().hex[:6],
        "strategy": strategy, "preference": preference,
        "snapshot_count": panel.universe_size, "after_filter_count": after_filter_count,
        "llm_ranked": rr["llm_ranked"], "llm_selection_logic": rr["llm_selection_logic"],
        "llm_portfolio_risk": rr["llm_portfolio_risk"],
        "warnings": warnings, "source_errors": [],
    }
```

并更新 `__init__.py`：
```python
from .engine import run_screen, ScreenInputError  # noqa: F401
```

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_engine.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/stock_screener/engine.py src/services/stock_screener/__init__.py tests/test_screen_engine.py
git commit -m "feat: add screen engine orchestration with input validation and preference filter"
```

---

## Phase 6：异步 job + API 端点

### Task 8: job store 泛化改名

**Files:**
- Create: `src/services/screen_jobs.py`（内容自 `alphasift_screen_jobs.py` 迁移、泛化）
- Delete: `src/services/alphasift_screen_jobs.py`（Task 10 统一删）
- Test: `tests/test_screen_jobs.py`

泛化点：`ScreenJob` 增加 `preference: Optional[str]`；`submit(strategy, preference, max_results, run_fn)`；`run_fn` 以 `strategy/preference/max_results` 关键字调用；幂等键改为"有进行中任务即复用"（沿用原逻辑）；类名 `ScreenJobStore`，日志文案去掉 "AlphaSift"。

- [ ] **Step 1: 写失败测试**
```python
# tests/test_screen_jobs.py
from src.services.screen_jobs import ScreenJobStore

def test_submit_and_get_completes():
    store = ScreenJobStore()
    def run_fn(strategy, preference, max_results):
        return {"candidates": [], "strategy": strategy, "preference": preference}
    job = store.submit("ma_golden_cross", "科技", 20, run_fn)
    assert job.status == "pending"
    import time
    for _ in range(50):
        cur = store.get(job.job_id)
        if cur.status in ("completed", "failed"):
            break
        time.sleep(0.05)
    done = store.get(job.job_id)
    assert done.status == "completed"
    assert done.result["preference"] == "科技"

def test_idempotent_reuse_active_job():
    store = ScreenJobStore()
    import threading
    gate = threading.Event()
    def run_fn(strategy, preference, max_results):
        gate.wait(2); return {"candidates": []}
    j1 = store.submit("a", None, 5, run_fn)
    j2 = store.submit("b", "x", 5, run_fn)   # 进行中 → 复用 j1
    assert j2.job_id == j1.job_id
    gate.set()
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError。

- [ ] **Step 3: 实现 `screen_jobs.py`** — 复制 `alphasift_screen_jobs.py` 全文，做如下改动：
  - `ScreenJob`：去掉 `market` 字段，加 `strategy: Optional[str] = None`、`preference: Optional[str] = None`；保留 `max_results`。
  - 类名 `AlphaSiftScreenJobStore` → `ScreenJobStore`；线程名前缀 `screen_`。
  - `submit(self, strategy, preference, max_results, run_fn)`；构造 `ScreenJob(job_id=..., strategy=strategy, preference=preference, max_results=max_results)`。
  - `_run` 中 `run_fn(strategy=job.strategy, preference=job.preference, max_results=job.max_results)`。
  - 所有日志文案 "AlphaSift 选股" → "选股"。

- [ ] **Step 4: 跑测试确认通过** — `python -m pytest tests/test_screen_jobs.py -v`，预期 PASS。

- [ ] **Step 5: commit**
```bash
git add src/services/screen_jobs.py tests/test_screen_jobs.py
git commit -m "feat: generalize async screen job store (strategy + preference)"
```

### Task 9: API 端点 `screen.py`

**Files:**
- Create: `api/v1/endpoints/screen.py`
- Modify: API router 注册处（找到 `alphasift` 路由注册的文件，替换为 `screen`）
- Test: `tests/test_screen_api.py`

端点（spec §3.7）：`GET /api/v1/screen/strategies`、`POST /api/v1/screen/jobs`、`GET /api/v1/screen/jobs/{job_id}`。沿用现有 `tests/test_alphasift_api.py` 的测试风格（直接调用端点函数，不用 TestClient）。

- [ ] **Step 1: 写失败测试**
```python
# tests/test_screen_api.py
import asyncio
import time
import pytest
from api.v1.endpoints import screen as ep
from src.services.screen_jobs import ScreenJobStore

@pytest.fixture(autouse=True)
def _reset_store_and_stub(monkeypatch):
    # 单例跨用例串扰 + 幂等复用进行中 job → 每个用例必须重置
    ScreenJobStore._instance = None
    # 端点用 `from src.services.stock_screener import run_screen`，故 patch 端点模块属性
    monkeypatch.setattr(ep, "run_screen",
                        lambda **k: {"enabled": True, "candidates": [], "candidateCount": 0})
    yield
    ScreenJobStore._instance = None

def _wait(job_id):
    store = ScreenJobStore.get_instance()
    for _ in range(100):
        j = store.get(job_id)
        if j and j.status in ("completed", "failed"):
            return j
        time.sleep(0.02)
    return store.get(job_id)

def test_strategies_lists_eight():
    res = asyncio.run(ep.list_strategies())
    ids = {s["id"] for s in res["strategies"]}
    assert {"ma_golden_cross","volume_breakout","bottom_volume","shrink_pullback",
            "one_yang_three_yin","growth_quality","box_oscillation","bull_trend"} <= ids
    assert all("tradingStyle" in s or "trading_style" in s for s in res["strategies"])

def test_submit_requires_strategy_or_preference():
    with pytest.raises(Exception):
        asyncio.run(ep.submit_screen_job(ep.ScreenJobRequest(strategy="", preference="", max_results=20)))

def test_submit_returns_job_id():
    req = ep.ScreenJobRequest(strategy="ma_golden_cross", preference="", max_results=5)
    res = asyncio.run(ep.submit_screen_job(req))
    jid = res.get("jobId") or res.get("job_id")
    assert jid
    done = _wait(jid)               # 等后台 job 落定（已 stub run_screen，不联网）
    assert done.status == "completed"

def test_get_job_pure_memory():
    with pytest.raises(Exception):  # 不存在的 job 返回 404
        asyncio.run(ep.get_screen_job("nonexistent"))
```

- [ ] **Step 2: 跑测试确认失败** — 预期 ImportError。

- [ ] **Step 3: 实现 `screen.py`**
```python
# -*- coding: utf-8 -*-
"""自研选股 API：策略列表、异步选股 job 提交与查询。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agent.skills.base import load_skills_from_directory, _BUILTIN_SKILLS_DIR
from src.services.screen_jobs import ScreenJobStore
from src.services.stock_screener import run_screen, ScreenInputError

logger = logging.getLogger(__name__)
# prefix 不在此写：与现有所有端点一致，由 api/v1/router.py 的 include_router(prefix="/screen") 提供
router = APIRouter(tags=["screen"])

_ONLINE_STRATEGIES = [
    "ma_golden_cross", "volume_breakout", "bottom_volume", "shrink_pullback",
    "one_yang_three_yin", "growth_quality", "box_oscillation", "bull_trend",
]


class ScreenJobRequest(BaseModel):
    strategy: Optional[str] = Field(default=None, max_length=64)
    preference: Optional[str] = Field(default=None, max_length=500)
    max_results: int = Field(default=20, ge=1, le=100)


@router.get("/strategies")
async def list_strategies():
    by_name = {s.name: s for s in load_skills_from_directory(_BUILTIN_SKILLS_DIR)}
    strategies = []
    for sid in _ONLINE_STRATEGIES:
        sk = by_name.get(sid)
        if not sk:
            continue
        strategies.append({"id": sk.name, "name": sk.display_name, "category": sk.category,
                           "description": sk.description,
                           "trading_style": getattr(sk, "trading_style", "")})
    return {"enabled": True, "strategies": strategies, "strategyCount": len(strategies)}


@router.post("/jobs")
async def submit_screen_job(request: ScreenJobRequest):
    strategy = (request.strategy or "").strip() or None
    preference = (request.preference or "").strip() or None
    if not strategy and not preference:
        raise HTTPException(status_code=400, detail="策略和用户偏好至少填写一个")

    def _run(strategy, preference, max_results):
        try:
            return run_screen(strategy=strategy, preference=preference, max_results=max_results)
        except ScreenInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    store = ScreenJobStore.get_instance()
    job = store.submit(strategy, preference, request.max_results, _run)
    return {"jobId": job.job_id, "status": job.status}


@router.get("/jobs/{job_id}")
async def get_screen_job(job_id: str):
    store = ScreenJobStore.get_instance()
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="选股任务不存在或已过期")
    payload = {"jobId": job.job_id, "status": job.status, "error": job.error}
    if job.result:
        payload.update(job.result)
    return payload
```

- [ ] **Step 4: 注册路由（共两处，缺一会 import 崩）** — 先 `grep -rn "alphasift" api/v1/` 定位，真实代码有两处引用：
  1. `api/v1/router.py`：import 行去掉 `alphasift`、加 `screen`；删 alphasift 的 `include_router`，新增（**register 处带 prefix，screen.py 内不带**）：
     ```python
     router.include_router(screen.router, prefix="/screen")
     ```
  2. `api/v1/endpoints/__init__.py`：把 `from . import alphasift` 与 `__all__` 中的 `alphasift` 改为 `screen`。
  > ⚠️ 若只改 router.py 而漏 `__init__.py`，待 Task 12 删除 `alphasift.py` 后整个 `api.v1.endpoints` 包 import 即崩、ci_gate 直接红。

- [ ] **Step 5: 跑测试确认通过** — `python -m pytest tests/test_screen_api.py -v`，预期 PASS（注意：`submit` 测试会真正异步触发 `run_screen`，可对 `engine.fetch_market_panel` monkeypatch 避免真实网络）。

- [ ] **Step 6: commit**
```bash
git add api/v1/endpoints/screen.py tests/test_screen_api.py <router_file>
git commit -m "feat: add /screen API endpoints (strategies, async jobs)"
```

---

## Phase 7：前端

> **⚠️ 前端 AlphaSift 耦合面远不止 StockScreeningPage（prd-reviewer 实测命中 ~10 个文件）。删除一个被多处 import/mock 的模块，必须先全量盘点。**
>
> **产品决策（已拍板）：**
> - **选股入口常驻显示**：原来选股菜单受 `alphasiftApi.getStatus().enabled` 门控。自研选股无需安装、无外部依赖 → 选股入口**常驻**，移除 status 门控与相关 CONFIG_CHANGED 事件。
> - **SettingsPage 的 AlphaSift 设置整块移除**（启用开关/安装 UI/状态）——自研选股无配置开关。
> - **市场选择**：后端固定 `cn`，前端移除 `MARKETS` 下拉与 `market` state。

### Task 10.0: 前端 AlphaSift 影响面盘点（先于任何前端改动）

**Files:** 只读盘点

- [ ] **Step 1: 全量定位**
```bash
cd apps/dsa-web && grep -rln "alphasift\|AlphaSift\|ALPHASIFT" src/
```
预期命中并需处理（实现者以实际 grep 为准）：
| 文件 | 处理 |
|------|------|
| `src/api/alphasift.ts` | → 改名 `screen.ts`（Task 10） |
| `src/pages/StockScreeningPage.tsx` | 改造（Task 11） |
| `src/components/layout/SidebarNav.tsx` | 移除选股菜单的 alphasift status 门控，改常驻；删 `getStatus`/CONFIG_CHANGED 监听 |
| `src/pages/SettingsPage.tsx` + `src/locales/settingsHelp.ts` | 移除 AlphaSift 启用/安装设置项与文案 |
| `src/api/error.ts` | 移除 `alphasift_install_*` 错误码分支 |
| `src/api/__tests__/alphasift.test.ts` | 删除或改写为 `screen.test.ts` |
| `src/pages/__tests__/StockScreeningPage.test.tsx` | 改：删"市场下拉只含 cn"/`market:'cn'` 入参断言，改用新字段 |
| `src/pages/__tests__/SettingsPage.test.tsx` | 删 AlphaSift 设置项断言 |
| `src/components/layout/__tests__/SidebarNav.test.tsx` | 改：选股菜单常驻断言 |
- [ ] **Step 2: 逐文件列出改动清单**（写到 commit message 或临时笔记），确保后续 Task 10/11/12 不遗漏；删源文件前先改完所有 import 引用（删除顺序见全局约定）。

### Task 10: `screen.ts` API 层

**Files:**
- Create: `apps/dsa-web/src/api/screen.ts`
- Test: 手动 build（前端无该模块单测，沿用现状）

- [ ] **Step 1: 实现 `screen.ts`** — 以 `alphasift.ts` 为基础，保留 `Candidate`/`ScreenResponse`/`Strategy` 类型形状（增字段 `tradingStyle?: string` 到 Strategy，`preference?: string` 到 ScreenResponse），API 改为：
```typescript
import apiClient from './index';
import { toCamelCase } from './utils';

const SCREEN_JOB_API_TIMEOUT_MS = 30000;

export type ScreenCandidate = {
  rank: number; code: string; name: string; score?: number | null;
  screenScore?: number | null; reason: string;
  llmThesis?: string; llmRisks?: string[]; llmStyleFit?: string;
  price?: number | null; changePct?: number | null; amount?: number | null;
  industry?: string; raw: Record<string, unknown>;
};
export type ScreenStrategy = {
  id: string; name: string; description: string; category?: string; tradingStyle?: string;
};
export type ScreenStrategiesResponse = { enabled: boolean; strategies: ScreenStrategy[]; strategyCount: number; };
export type ScreenResponse = {
  enabled: boolean; candidates: ScreenCandidate[]; candidateCount: number;
  runId?: string; strategy?: string | null; preference?: string | null;
  snapshotCount?: number; afterFilterCount?: number; llmRanked?: boolean;
  llmSelectionLogic?: string; llmPortfolioRisk?: string; warnings?: string[]; sourceErrors?: string[];
};
export type ScreenJobSubmit = { jobId: string; status: 'pending'|'running'|'completed'|'failed'; };
export type ScreenJobResult = ScreenResponse & { jobId: string; status: 'pending'|'running'|'completed'|'failed'; error?: string; };

export const screenApi = {
  async getStrategies(): Promise<ScreenStrategiesResponse> {
    const r = await apiClient.get<Record<string, unknown>>('/api/v1/screen/strategies');
    return toCamelCase<ScreenStrategiesResponse>(r.data);
  },
  async submitScreenJob(p: { strategy?: string; preference?: string; maxResults: number }): Promise<ScreenJobSubmit> {
    const r = await apiClient.post<Record<string, unknown>>('/api/v1/screen/jobs',
      { strategy: p.strategy || null, preference: p.preference || null, max_results: p.maxResults },
      { timeout: SCREEN_JOB_API_TIMEOUT_MS });
    return toCamelCase<ScreenJobSubmit>(r.data);
  },
  async getScreenJob(jobId: string): Promise<ScreenJobResult> {
    const r = await apiClient.get<Record<string, unknown>>(`/api/v1/screen/jobs/${jobId}`,
      { timeout: SCREEN_JOB_API_TIMEOUT_MS });
    return toCamelCase<ScreenJobResult>(r.data);
  },
};
```

- [ ] **Step 2: commit**
```bash
git add apps/dsa-web/src/api/screen.ts
git commit -m "feat: add screen API client (web)"
```

### Task 11: StockScreeningPage 改造（偏好框 + 策略卡片操盘风格 + 校验）

**Files:**
- Modify: `apps/dsa-web/src/pages/StockScreeningPage.tsx`

- [ ] **Step 1: 改 import 与类型** — 把 `from '../api/alphasift'` 改为 `from '../api/screen'`，类型 `AlphaSiftStrategy`→`ScreenStrategy`、`AlphaSiftCandidate`→`ScreenCandidate`、`AlphaSiftScreenResponse`→`ScreenResponse`，`alphasiftApi`→`screenApi`。`getCandidateReason` 里 "AlphaSift 返回候选..." 文案改为"选股返回候选，但没有给出文字摘要..."。

- [ ] **Step 1b: 候选卡片字段对齐（关键，否则 build 报 TS 错或恒空）** — 新 `ScreenCandidate` **不再有** `llmScore/riskLevel/riskFlags/llmCatalysts/factorScores` 等 AlphaSift 专属字段。移除结果表对这些字段的渲染（`StockScreeningPage.tsx` 约第 68/512/515/553/588 行），改用新字段：`score`(=signal_score)、`reason`、`llmThesis`、`llmRisks`、`llmStyleFit`、`changePct`、`amount`、`industry`。`formatScore`/`getFactorEntries` 等辅助函数入参类型同步改为 `ScreenCandidate`，删掉只服务于已移除字段的辅助函数。新增展示 `llmStyleFit`（与偏好/风格契合度）。

- [ ] **Step 2: 新增偏好 state 与输入框** — 在 `strategy` state 旁加：
```typescript
const [preference, setPreference] = useState('');
```
在策略选择区域下方加输入框（沿用页面现有表单样式）：
```tsx
<label className="block text-sm font-medium mb-1">用户偏好（可选）</label>
<textarea
  value={preference}
  onChange={(e) => setPreference(e.target.value)}
  maxLength={500}
  placeholder="例如：喜欢科技股、偏好抄底、规避高估值。留空则只按所选策略选股。"
  className="w-full rounded-md border px-3 py-2 text-sm"
  rows={2}
/>
<p className="text-xs text-gray-500 mt-1">偏好与策略冲突时，将在候选范围内优先满足你的偏好。</p>
```

- [ ] **Step 3: 提交前校验** — 在提交 handler 开头加：
```typescript
if (!strategy && !preference.trim()) {
  setError('策略和用户偏好至少填写一个');
  return;
}
```
并把 `submitScreenJob` 调用参数改为 `{ strategy: strategy || undefined, preference: preference.trim() || undefined, maxResults }`（移除 `market` 必填，后端默认 cn）。

- [ ] **Step 4: 策略卡片展示操盘风格** — 在策略选择项渲染处，展示 `selectedStrategy?.tradingStyle`：
```tsx
{selectedStrategy?.tradingStyle && (
  <span className="ml-2 inline-block rounded bg-amber-50 px-2 py-0.5 text-xs text-amber-700">
    适合：{selectedStrategy.tradingStyle}
  </span>
)}
```

- [ ] **Step 5: 结果区展示偏好与选股逻辑** — 结果摘要处，若 `result.preference` 非空则展示"按偏好：{preference}"，`llmSelectionLogic` 展示为选股逻辑说明（页面已有 llm 字段渲染，沿用）。

- [ ] **Step 6: 移除市场下拉** — `StockScreeningPage.tsx` 删 `MARKETS` 常量（第 13 行）、`market` state（第 75 行）及市场选择 UI（第 379-382/439 行附近）；提交参数不再带 `market`（后端默认 cn）。

- [ ] **Step 7: 关联前端文件改动**（按 Task 10.0 盘点）—
  - `SidebarNav.tsx`：移除选股菜单的 `showAlphaSiftNav`/`getStatus` 门控与 `ALPHASIFT_CONFIG_CHANGED_EVENT`/`SYSTEM_CONFIG_CHANGED_EVENT` 监听，选股入口改常驻；
  - `SettingsPage.tsx` + `settingsHelp.ts`：移除 AlphaSift 启用/安装设置项与文案；
  - `api/error.ts`：移除 `alphasift_install_*` 错误码分支；
  - 对应测试文件（`StockScreeningPage.test.tsx`/`SettingsPage.test.tsx`/`SidebarNav.test.tsx`/`alphasift.test.ts`）同步更新（删市场/AlphaSift 断言、改常驻断言、改写或删 alphasift.test）。

- [ ] **Step 8: 前端全量验证**
```bash
cd apps/dsa-web && npm ci && npm run lint && npm run build && npm run test
```
预期：lint / build / vitest 均通过（vitest 必跑——本改动删除了被多个 test mock 的 alphasift 模块，仅 lint+build 漏不掉的回归靠它兜住）。

- [ ] **Step 9: commit**
```bash
git add apps/dsa-web/src/
git commit -m "feat: add preference input and trading-style; align screening UI to new API"
```

---

## Phase 8：移除 AlphaSift + 文档 + 验证

### Task 12: 删除 AlphaSift 残留

**Files:**
- Delete: `api/v1/endpoints/alphasift.py`、`src/services/alphasift_screen_jobs.py`、`apps/dsa-web/src/api/alphasift.ts`、`tests/test_alphasift_api.py`
- Modify: 任何残留 import / 配置项（`ALPHASIFT_ENABLED` 等）

- [ ] **Step 1: 全局搜索残留**
```bash
grep -rn "alphasift\|AlphaSift\|ALPHASIFT" --include=*.py --include=*.ts --include=*.tsx api/ src/ apps/dsa-web/src/ tests/ | grep -v "docs/"
```
- [ ] **Step 2: 删除文件 + 清理 import**（逐个处理上面 grep 命中；前端确认无组件再 import `alphasift`；后端确认 router 不再注册 alphasift；移除对 alphasift 包的安装/状态/install 逻辑与相关 `ALPHASIFT_*` 配置读取）。
- [ ] **Step 3: 跑后端 gate** — `./scripts/ci_gate.sh`，预期通过。
- [ ] **Step 4: 前端 build** — `cd apps/dsa-web && npm run lint && npm run build`，预期通过。
- [ ] **Step 5: commit**
```bash
git add -A
git commit -m "chore: remove AlphaSift screening channel and residue"
```

### Task 13: 文档 / 配置 / CHANGELOG

**Files:**
- Modify: `.env.example`（移除 `ALPHASIFT_*`；选股无需新配置则说明）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平追加）
- Modify: 相关 `docs/*.md`（如 `docs/alphasift-integration.md` 标记下线或替换为自研选股说明）
- Modify: 外层维护文档 `/Volumes/Mac硬盘/project/股票分析部署/CLAUDE.md`（移除 AlphaSift 分叉条目，新增自研选股引擎说明，强调不触碰数据源铁律）

- [ ] **Step 1: CHANGELOG 追加**（`[Unreleased]` 段，扁平格式）
```
- [新功能] 自研选股引擎替换 AlphaSift：全市场量化足切 + LLM 轻量重排，支持 8 策略与用户偏好
- [改进] 选股端点改名为 /api/v1/screen/*，移除 AlphaSift 依赖
```
- [ ] **Step 2: `.env.example`** — 移除 `ALPHASIFT_*` 行；如选股复用现有 Tushare/LLM 配置则补一行注释说明无需新增配置。
- [ ] **Step 3: docs 更新** — `docs/alphasift-integration.md` 顶部加下线说明并指向新选股文档（或新建 `docs/stock-screener.md` 描述 8 策略、偏好用法、两段式与降级）。
- [ ] **Step 4: 外层 CLAUDE.md** — 按 spec §7 更新。
- [ ] **Step 5: commit**
```bash
git add .env.example docs/ "/Volumes/Mac硬盘/project/股票分析部署/CLAUDE.md"
git commit -m "docs: document self-built screener; remove AlphaSift references"
```

### Task 14: 全量验证 + 自动记忆更新

- [ ] **Step 1: 后端全量 gate** — `./scripts/ci_gate.sh` + `python -m pytest tests/test_screen_*.py -v`，预期全绿。
- [ ] **Step 2: 前端** — `cd apps/dsa-web && npm run lint && npm run build`，预期通过。
- [ ] **Step 3: 数据源铁律自检** — 确认未改动 `data_provider/base.py` 的 `_init_default_fetchers`/`get_realtime_quote`/`get_daily_data`/路由优先级（`git diff main -- data_provider/` 应为空或仅无关）。
- [ ] **Step 4: 更新自动记忆** — 更新 `dsa-alphasift-async-divergence`（标记 AlphaSift 下线、被自研引擎替换），必要时新增 `dsa-self-built-screener` 记忆与 MEMORY.md 指针。

---

## 部署（开发 + 测试通过后）

> 用户已授权：开发完成、测试通过后**直接部署上线**，上线后再汇报。

- [ ] **Step 1: 合并到 main** — `git checkout main && git merge --no-ff feat/self-built-screener`。
- [ ] **Step 2: push** — `git push origin main`（这是远端变更，已获用户授权"直接部署上线"）。
- [ ] **Step 3: 服务器更新** — ssh 上去 `cd /opt/daily_stock_analysis && git pull`，保留 `docker/docker-compose.yml` 的 `env_file`/`ENV_FILE` 本地改动。
- [ ] **Step 4: 重建容器** — `docker compose -f docker/docker-compose.yml up -d --build`（server + analyzer）。
- [ ] **Step 5: 生产烟雾测试** —
  - `curl` 健康检查 200；
  - 登录态下 `POST /api/v1/screen/jobs`（如 `ma_golden_cross`），轮询 `GET /api/v1/screen/jobs/{id}` 直至 completed，确认返回候选；
  - 确认实时/日K 来源仍为 TickFlow、Tushare 各能力仍优先、akshare 末位（按外层 CLAUDE.md 铁律自检）。
- [ ] **Step 6: 汇报用户** — 改了什么 / 验证情况 / 未验证项 / 风险点 / 回滚方式（回滚 = 服务器 `git reset --hard <旧SHA>` + 重建容器）。

---

## Self-Review 检查（计划编写者已核对）

- **Spec 覆盖**：移除 AlphaSift(Task12) / 8策略(Task3-5) / 端点改名(Task9) / 用户偏好(Task7,11) / 偏好与策略至少一个(Task7,9,11) / 冲突偏好优先(Task6 ranker prompt + Task7 board filter) / 卡片操盘风格(Task5,11) / 不触碰铁律(Task14 Step3) / 降级(Task6,7) / 异步job(Task8) / 文档(Task13) — 全部有对应任务。
- **类型一致**：`STRATEGY_SCORERS` / `MarketPanel` / `run_screen` / `ScreenInputError` / `ScreenJobStore` / `rerank` / candidate 字段在各 Task 间签名一致。
- **无占位符**：核心后端逻辑均给完整代码；前端给完整 API 层与明确修改片段。
- **已知务实取舍**：scorer 量化阈值为工程近似（取自 YAML 文字判据），上线后可按实际命中率微调；偏好板块识别用行业子串匹配（简单稳健，复杂语义留给第2段 LLM）。
