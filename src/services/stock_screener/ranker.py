# -*- coding: utf-8 -*-
"""自研选股第2段：候选池 LLM 轻量重排。复用现有 LiteLLM 通道（运行时走 MiMo）。"""
from __future__ import annotations

import json
import logging
import random
import re
from typing import List, Optional

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
