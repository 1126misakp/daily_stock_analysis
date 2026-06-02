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
