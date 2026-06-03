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
                # 本监控仅 A 股（TickFlow 分钟K 仅承接 A 股）。
                # position["market"] 取值来自 VALID_MARKETS={"cn","hk","us"}（小写）；
                # 只保留 cn（或缺失时回退账户 market 仍为 cn/空），跳过 hk/us。
                market = str(position.get("market") or account.get("market") or "").lower()
                if market and market != "cn":
                    continue
                symbol = position.get("symbol")
                if symbol:
                    out.append(symbol)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("[IntradayVolume] 读取持仓快照失败: %s", exc)
        return []
