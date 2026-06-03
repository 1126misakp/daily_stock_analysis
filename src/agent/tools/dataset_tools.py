# -*- coding: utf-8 -*-
"""MCP 中转站新增数据工具。

全部经 DataFetcherManager 取数，继承数据源铁律优先级（TickFlow/Tushare 先、
akshare 末位）。DataFrame 结果统一序列化为 records；None/空 → {"info": ...}。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter

logger = logging.getLogger(__name__)

_MAX_ROWS = 60


def _get_fetcher_manager():
    """复用 data_tools 已有的 manager 单例，避免进程内重复初始化 Tushare/TickFlow
    （AGENTS.md：优先复用现有入口，不新增平行实现）。"""
    from src.agent.tools.data_tools import _get_fetcher_manager as _shared
    return _shared()


def _df_to_records(df) -> Optional[List[Dict[str, Any]]]:
    if df is None or getattr(df, "empty", True):
        return None
    return df.head(_MAX_ROWS).to_dict(orient="records")


# ---------- handlers ----------

def _handle_single_code_df(method_name: str, stock_code: str, **kwargs) -> Dict[str, Any]:
    try:
        mgr = _get_fetcher_manager()
        df = getattr(mgr, method_name)(stock_code, **kwargs)
        recs = _df_to_records(df)
        if recs is None:
            return {"info": f"No data for {stock_code} ({method_name})."}
        return {"stock_code": stock_code, "items": recs}
    except Exception:
        logger.warning(f"[dataset_tools] {method_name} error", exc_info=True)
        return {"error": f"Failed to fetch {method_name} for {stock_code}."}


def _handle_repurchase(stock_code: str, start_date: str = "", end_date: str = "") -> Dict[str, Any]:
    return _handle_single_code_df(
        "get_repurchase", stock_code,
        start_date=start_date or None, end_date=end_date or None)


def _handle_dragon_tiger(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_dragon_tiger_context(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] dragon_tiger error", exc_info=True)
        return {"error": f"Failed to fetch dragon_tiger for {stock_code}."}


def _handle_risk_assessment(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_risk_context(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] risk_context error", exc_info=True)
        return {"error": f"Failed to fetch risk for {stock_code}."}


def _handle_stock_sectors(stock_code: str) -> Dict[str, Any]:
    try:
        boards = _get_fetcher_manager().get_belong_boards(stock_code) or []
        return {"stock_code": stock_code, "boards": boards}
    except Exception:
        logger.warning("[dataset_tools] belong_boards error", exc_info=True)
        return {"error": f"Failed to fetch sectors for {stock_code}."}


def _handle_intraday_kline(stock_code: str, period: str = "5m", count: int = 240) -> Dict[str, Any]:
    try:
        df = _get_fetcher_manager().get_intraday_kline(stock_code, period=period, count=count)
        recs = _df_to_records(df)
        if recs is None:
            return {"info": f"No intraday kline for {stock_code}."}
        return {"stock_code": stock_code, "period": period, "items": recs}
    except Exception:
        logger.warning("[dataset_tools] intraday_kline error", exc_info=True)
        return {"error": f"Failed to fetch intraday kline for {stock_code}."}


def _handle_order_book(stock_code: str) -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_order_book(stock_code) or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] order_book error", exc_info=True)
        return {"error": f"Failed to fetch order book for {stock_code}."}


def _handle_limit_up_pool(date: str = "", n: int = 20) -> Dict[str, Any]:
    try:
        data = _get_fetcher_manager().get_limit_up_pool(date=date or None, n=n) or []
        return {"items": data}
    except Exception:
        logger.warning("[dataset_tools] limit_up_pool error", exc_info=True)
        return {"error": "Failed to fetch limit up pool."}


def _handle_hot_stocks(n: int = 10) -> Dict[str, Any]:
    try:
        return {"items": _get_fetcher_manager().get_hot_stocks(n=n) or []}
    except Exception:
        logger.warning("[dataset_tools] hot_stocks error", exc_info=True)
        return {"error": "Failed to fetch hot stocks."}


def _handle_concept_rankings(n: int = 5) -> Dict[str, Any]:
    try:
        top, bottom = _get_fetcher_manager().get_concept_rankings(n=n)
        return {"top": top or [], "bottom": bottom or []}
    except Exception:
        logger.warning("[dataset_tools] concept_rankings error", exc_info=True)
        return {"error": "Failed to fetch concept rankings."}


def _handle_market_stats() -> Dict[str, Any]:
    try:
        return _get_fetcher_manager().get_market_stats() or {"info": "no data"}
    except Exception:
        logger.warning("[dataset_tools] market_stats error", exc_info=True)
        return {"error": "Failed to fetch market stats."}


# ---------- ToolDefinition 工厂（单股 DataFrame 类，DRY）----------

_CODE_PARAM = ToolParameter(name="stock_code", type="string",
                            description="A股代码，如 '600519'", required=True)


def _make_single_code_df_tool(tool_name: str, method_name: str, description: str) -> ToolDefinition:
    def handler(stock_code: str, _m=method_name) -> Dict[str, Any]:
        return _handle_single_code_df(_m, stock_code)
    return ToolDefinition(name=tool_name, description=description,
                          parameters=[_CODE_PARAM], handler=handler, category="data")


_SINGLE_CODE_DF_SPECS = [
    ("get_income_statement", "get_income_statement", "获取个股利润表（按报告期，最近若干期）。"),
    ("get_cashflow_statement", "get_cashflow_statement", "获取个股现金流量表（按报告期）。"),
    ("get_financial_indicators", "get_fina_indicator", "获取个股财务指标（ROE/毛利率/资产负债率等）。"),
    ("get_pledge_detail", "get_pledge_detail", "获取个股股权质押明细。"),
    ("get_holder_trade", "get_holder_trade", "获取个股股东增减持记录。"),
    ("get_share_float", "get_share_float", "获取个股限售解禁明细。"),
]

# ---------- 显式 ToolDefinition（非统一签名）----------

get_repurchase_tool = ToolDefinition(
    name="get_repurchase",
    description="获取个股股份回购记录，可选起止日期(YYYYMMDD)。",
    parameters=[
        _CODE_PARAM,
        ToolParameter(name="start_date", type="string", description="起始日 YYYYMMDD", required=False),
        ToolParameter(name="end_date", type="string", description="截止日 YYYYMMDD", required=False),
    ],
    handler=_handle_repurchase, category="data",
)

get_dragon_tiger_tool = ToolDefinition(
    name="get_dragon_tiger",
    description="获取个股龙虎榜上榜信息（含机构席位、买卖额）。",
    parameters=[_CODE_PARAM], handler=_handle_dragon_tiger, category="data",
)

get_risk_assessment_tool = ToolDefinition(
    name="get_risk_assessment",
    description="获取个股风险与估值评估（估值水平、历史分位、风险信号）。",
    parameters=[_CODE_PARAM], handler=_handle_risk_assessment, category="data",
)

get_stock_sectors_tool = ToolDefinition(
    name="get_stock_sectors",
    description="获取个股所属板块/概念列表。",
    parameters=[_CODE_PARAM], handler=_handle_stock_sectors, category="data",
)

get_intraday_kline_tool = ToolDefinition(
    name="get_intraday_kline",
    description="获取个股分钟级K线（period: 5m/15m/30m/60m）。",
    parameters=[
        _CODE_PARAM,
        ToolParameter(name="period", type="string", description="周期", required=False,
                      enum=["5m", "15m", "30m", "60m"], default="5m"),
        ToolParameter(name="count", type="integer", description="返回根数(默认240)", required=False, default=240),
    ],
    handler=_handle_intraday_kline, category="data",
)

get_order_book_tool = ToolDefinition(
    name="get_order_book",
    description="获取个股五档盘口（买卖五档量价）。",
    parameters=[_CODE_PARAM], handler=_handle_order_book, category="data",
)

get_limit_up_pool_tool = ToolDefinition(
    name="get_limit_up_pool",
    description="获取当日涨停池/连板梯队。",
    parameters=[
        ToolParameter(name="date", type="string", description="日期 YYYYMMDD，默认当日", required=False),
        ToolParameter(name="n", type="integer", description="返回条数(默认20)", required=False, default=20),
    ],
    handler=_handle_limit_up_pool, category="data",
)

get_hot_stocks_tool = ToolDefinition(
    name="get_hot_stocks",
    description="获取市场人气股榜。",
    parameters=[ToolParameter(name="n", type="integer", description="返回条数(默认10)", required=False, default=10)],
    handler=_handle_hot_stocks, category="data",
)

get_concept_rankings_tool = ToolDefinition(
    name="get_concept_rankings",
    description="获取概念/题材涨跌榜（领涨 top 与领跌 bottom）。",
    parameters=[ToolParameter(name="n", type="integer", description="每侧条数(默认5)", required=False, default=5)],
    handler=_handle_concept_rankings, category="data",
)

get_market_stats_tool = ToolDefinition(
    name="get_market_stats",
    description="获取全市场涨跌家数统计（涨/跌/平/涨停/跌停/成交额）。",
    parameters=[], handler=_handle_market_stats, category="data",
)


ALL_DATASET_TOOLS: List[ToolDefinition] = (
    [_make_single_code_df_tool(t, m, d) for (t, m, d) in _SINGLE_CODE_DF_SPECS]
    + [
        get_repurchase_tool,
        get_dragon_tiger_tool,
        get_risk_assessment_tool,
        get_stock_sectors_tool,
        get_intraday_kline_tool,
        get_order_book_tool,
        get_limit_up_pool_tool,
        get_hot_stocks_tool,
        get_concept_rankings_tool,
        get_market_stats_tool,
    ]
)
