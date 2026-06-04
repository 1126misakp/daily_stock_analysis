# -*- coding: utf-8 -*-
"""
AkShare fundamental adapter (fail-open).

This adapter intentionally uses capability probing against multiple AkShare
endpoint candidates. It should never raise to caller; partial data is allowed.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

_DIVIDEND_KEYWORD_MAP: Dict[str, List[str]] = {
    "per_share": [
        "每股派息",
        "每股现金红利",
        "每股分红",
        "每股派现",
        "派现(元/股)",
        "派息(元/股)",
        "税前派息(元/股)",
        "现金分红(税前)",
    ],
    "plan_text": [
        "分配方案",
        "分红方案",
        "实施方案",
        "派息方案",
        "方案",
        "预案",
        "方案说明",
    ],
    "ex_dividend_date": ["除权除息日", "除息日", "除权日", "除权除息", "除息日期"],
    "record_date": ["股权登记日", "登记日"],
    "announce_date": ["公告日期", "公告日", "实施公告日", "预案公告日"],
    "report_date": ["报告期", "报告日期", "截止日期", "统计截止日期"],
}


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort float conversion."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    s = str(value).strip().replace(",", "").replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value)
    except Exception:
        return None
    if pd.isna(parsed):
        return None
    try:
        return parsed.to_pydatetime()
    except Exception:
        return None


def _normalize_code(raw: Any) -> str:
    s = _safe_str(raw).upper()
    if "." in s:
        s = s.split(".", 1)[0]
    s = re.sub(r"^(SH|SZ|BJ)", "", s)
    return s


def _pick_by_keywords(row: pd.Series, keywords: List[str]) -> Optional[Any]:
    """
    Return first non-empty row value whose column name contains any keyword.
    """
    for col in row.index:
        col_s = str(col)
        if any(k in col_s for k in keywords):
            val = row.get(col)
            if val is not None and str(val).strip() not in ("", "-", "nan", "None"):
                return val
    return None


def _parse_dividend_plan_to_per_share(plan_text: str) -> Optional[float]:
    """Parse per-share cash dividend from Chinese plan text."""
    text = _safe_str(plan_text)
    if not text:
        return None

    for pattern in (
        r"(?:每)?\s*10\s*股?\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"10\s*派\s*([0-9]+(?:\.[0-9]+)?)\s*元",
    ):
        match = re.search(pattern, text)
        if match:
            parsed = _safe_float(match.group(1))
            if parsed is not None and parsed > 0:
                return parsed / 10.0

    match_per_share = re.search(r"每\s*股\s*派(?:发)?\s*([0-9]+(?:\.[0-9]+)?)\s*元", text)
    if match_per_share:
        parsed = _safe_float(match_per_share.group(1))
        if parsed is not None and parsed > 0:
            return parsed
    return None


def _extract_cash_dividend_per_share(row: pd.Series) -> Optional[float]:
    """Extract pre-tax cash dividend per share from a row."""
    plan_text = _safe_str(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["plan_text"]))
    # Keep pre-tax semantics; skip explicit after-tax plans unless pre-tax marker exists.
    if "税后" in plan_text and "税前" not in plan_text and "含税" not in plan_text:
        return None

    direct = _safe_float(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["per_share"]))
    if direct is not None and direct > 0:
        return direct
    return _parse_dividend_plan_to_per_share(plan_text)


def _filter_rows_by_code(df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "symbol", "ts_code"))]
    if not code_cols:
        return df

    target = _normalize_code(stock_code)
    for col in code_cols:
        try:
            series = df[col].astype(str).map(_normalize_code)
            filtered = df[series == target]
            if not filtered.empty:
                return filtered
        except Exception:
            continue
    return pd.DataFrame()


def _normalize_report_date(value: Any) -> Optional[str]:
    parsed = _safe_datetime(value)
    return parsed.date().isoformat() if parsed else None


def _build_dividend_payload(
    dividend_df: pd.DataFrame,
    stock_code: str,
    max_events: int = 5,
) -> Dict[str, Any]:
    work_df = _filter_rows_by_code(dividend_df, stock_code)
    if work_df.empty:
        return {}

    now_date = datetime.now().date()
    ttm_start_date = now_date - timedelta(days=365)
    dedupe_keys = set()
    events: List[Dict[str, Any]] = []

    for _, row in work_df.iterrows():
        if not isinstance(row, pd.Series):
            continue
        ex_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["ex_dividend_date"]))
        record_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["record_date"]))
        announce_dt = _safe_datetime(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["announce_date"]))
        event_dt = ex_dt or record_dt or announce_dt
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if event_date > now_date:
            continue

        per_share = _extract_cash_dividend_per_share(row)
        if per_share is None or per_share <= 0:
            continue

        dedupe_key = (event_date.isoformat(), round(per_share, 6))
        if dedupe_key in dedupe_keys:
            continue
        dedupe_keys.add(dedupe_key)

        events.append(
            {
                "event_date": event_date.isoformat(),
                "ex_dividend_date": ex_dt.date().isoformat() if ex_dt else None,
                "record_date": record_dt.date().isoformat() if record_dt else None,
                "announcement_date": announce_dt.date().isoformat() if announce_dt else None,
                "cash_dividend_per_share": round(per_share, 6),
                "is_pre_tax": True,
            }
        )

    if not events:
        return {}

    events.sort(key=lambda item: item.get("event_date") or "", reverse=True)
    ttm_events: List[Dict[str, Any]] = []
    for item in events:
        event_dt = _safe_datetime(item.get("event_date"))
        if event_dt is None:
            continue
        event_date = event_dt.date()
        if ttm_start_date <= event_date <= now_date:
            ttm_events.append(item)

    return {
        "events": events[:max(1, max_events)],
        "ttm_event_count": len(ttm_events),
        "ttm_cash_dividend_per_share": (
            round(sum(float(item.get("cash_dividend_per_share") or 0.0) for item in ttm_events), 6)
            if ttm_events else None
        ),
        "coverage": "cash_dividend_pre_tax",
        "as_of": now_date.isoformat(),
    }


def _extract_latest_row(df: pd.DataFrame, stock_code: str) -> Optional[pd.Series]:
    """
    Select the most relevant row for the given stock.
    """
    if df is None or df.empty:
        return None

    code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码", "ts_code", "symbol"))]
    target = _normalize_code(stock_code)
    if code_cols:
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                matched = df[series == target]
                if not matched.empty:
                    return matched.iloc[0]
            except Exception:
                continue
        return None

    # Fallback: use latest row
    return df.iloc[0]


class AkshareFundamentalAdapter:
    """AkShare adapter for fundamentals, capital flow and dragon-tiger signals.

    P1：注入 ``tushare_provider``（返回 manager 链里已实例化的 TushareFetcher
    或 None）后，资金流/龙虎榜/财务区块优先取 Tushare，失败回退原 akshare 候选。
    未注入（provider=None / 返回 None）时行为与纯 akshare 适配器一致。
    """

    def __init__(self, tushare_provider: Optional[Any] = None) -> None:
        # tushare_provider: Callable[[], Optional[TushareFetcher]]，延迟解析以复用
        # manager 链里已实例化的 Tushare（共享限频，见决策②）。
        self._tushare_provider = tushare_provider
        # top_list 按交易日缓存，避免多只自选股重复拉同一天全市场龙虎榜
        self._top_list_cache: Dict[str, Optional[pd.DataFrame]] = {}

    def _tushare(self) -> Optional[Any]:
        """解析可用的 TushareFetcher 实例；不可用返回 None。"""
        provider = getattr(self, "_tushare_provider", None)
        if provider is None:
            return None
        try:
            return provider()
        except Exception as exc:
            logger.debug("[fundamental] 解析 Tushare 实例失败: %s", exc)
            return None

    def _tushare_stock_flow(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """个股资金流：Tushare moneyflow → {main_net_inflow, inflow_5d, inflow_10d}。

        Tushare ``net_mf_amount`` 单位为万元，统一 ×1e4 转为元，与 akshare
        ``主力净流入-净额`` 口径对齐。无实例 / 无数据返回 None（回退 akshare）。
        """
        fetcher = self._tushare()
        if fetcher is None or not hasattr(fetcher, "get_moneyflow"):
            return None
        now = datetime.now()
        df = fetcher.get_moneyflow(
            stock_code,
            start_date=(now - timedelta(days=30)).strftime("%Y%m%d"),
            end_date=now.strftime("%Y%m%d"),
        )
        if (
            df is None
            or getattr(df, "empty", True)
            or "net_mf_amount" not in df.columns
            or "trade_date" not in df.columns
        ):
            return None
        work = df.sort_values("trade_date", ascending=False)
        net = pd.to_numeric(work["net_mf_amount"], errors="coerce").dropna()
        if net.empty:
            return None
        to_yuan = 1e4
        return {
            "main_net_inflow": float(net.iloc[0]) * to_yuan,
            "inflow_5d": float(net.head(5).sum()) * to_yuan,
            "inflow_10d": float(net.head(10).sum()) * to_yuan,
        }

    def _cached_top_list(self, fetcher: Any, trade_date: str) -> Optional[pd.DataFrame]:
        """按交易日缓存全市场龙虎榜，避免多只自选股重复拉同一天。"""
        if trade_date in self._top_list_cache:
            return self._top_list_cache[trade_date]
        df = fetcher.get_top_list(trade_date)
        self._top_list_cache[trade_date] = df
        return df

    def _tushare_dragon_tiger(
        self, stock_code: str, lookback_days: int
    ) -> Optional[Dict[str, Any]]:
        """龙虎榜：在 lookback 交易日窗口内统计个股上榜情况（Tushare top_list）。

        复用 TushareFetcher 的交易日历（_get_trade_dates）确定窗口内交易日，
        逐日（缓存）拉全市场龙虎榜按 ts_code 过滤。窗口内一条都没取到 → 返回
        None 回退 akshare。返回 {is_on_list, recent_count, latest_date}。
        """
        fetcher = self._tushare()
        if (
            fetcher is None
            or not hasattr(fetcher, "get_top_list")
            or not hasattr(fetcher, "_get_trade_dates")
        ):
            return None
        try:
            trade_dates = fetcher._get_trade_dates()
        except Exception as exc:
            logger.debug("[fundamental] Tushare 交易日历获取失败: %s", exc)
            return None
        if not trade_dates:
            return None

        cutoff = (datetime.now() - timedelta(days=max(1, lookback_days))).strftime("%Y%m%d")
        target = _normalize_code(stock_code)
        hit_dates: List[str] = []
        checked_any = False
        for td in trade_dates:
            if str(td) < cutoff:
                continue
            df = self._cached_top_list(fetcher, td)
            if df is None or getattr(df, "empty", True) or "ts_code" not in df.columns:
                continue
            checked_any = True
            codes = df["ts_code"].astype(str).map(_normalize_code)
            if bool((codes == target).any()):
                hit_dates.append(str(td))

        if not checked_any:
            return None

        hit_dates.sort(reverse=True)
        return {
            "is_on_list": bool(hit_dates),
            "recent_count": len(hit_dates),
            "latest_date": _normalize_report_date(hit_dates[0]) if hit_dates else None,
        }

    @staticmethod
    def _ts_num(value: Any) -> Optional[float]:
        """Tushare 数值归一：None / NaN → None，否则转 float。"""
        if value is None:
            return None
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        return _safe_float(value)

    @staticmethod
    def _latest_report_row(
        df: Optional[pd.DataFrame], prefer_consolidated: bool = False
    ) -> Optional[pd.Series]:
        """取最新报告期一行：优先合并报表(report_type=='1')，按 end_date 降序。"""
        if df is None or getattr(df, "empty", True) or "end_date" not in df.columns:
            return None
        work = df
        if prefer_consolidated and "report_type" in work.columns:
            consolidated = work[work["report_type"].astype(str) == "1"]
            if not consolidated.empty:
                work = consolidated
        work = work.sort_values("end_date", ascending=False)
        return work.iloc[0]

    def _tushare_growth(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """成长指标：Tushare fina_indicator 最新期 → growth 结构。"""
        fetcher = self._tushare()
        if fetcher is None or not hasattr(fetcher, "get_fina_indicator"):
            return None
        row = self._latest_report_row(fetcher.get_fina_indicator(stock_code))
        if row is None:
            return None
        revenue_yoy = self._ts_num(row.get("or_yoy"))
        if revenue_yoy is None:
            revenue_yoy = self._ts_num(row.get("tr_yoy"))
        net_profit_yoy = self._ts_num(row.get("netprofit_yoy"))
        if net_profit_yoy is None:
            net_profit_yoy = self._ts_num(row.get("dt_netprofit_yoy"))
        payload = {
            "revenue_yoy": revenue_yoy,
            "net_profit_yoy": net_profit_yoy,
            "roe": self._ts_num(row.get("roe")),
            "gross_margin": self._ts_num(row.get("grossprofit_margin")),
        }
        if all(v is None for v in payload.values()):
            return None
        return payload

    def _tushare_financial_report(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """财务报表：income(revenue/n_income_attr_p) + cashflow(n_cashflow_act)
        + fina_indicator(roe)，取各自最新合并报表期。"""
        fetcher = self._tushare()
        if fetcher is None or not hasattr(fetcher, "get_income_statement"):
            return None
        inc = self._latest_report_row(fetcher.get_income_statement(stock_code), prefer_consolidated=True)
        cf = self._latest_report_row(fetcher.get_cashflow_statement(stock_code), prefer_consolidated=True)
        fina = self._latest_report_row(fetcher.get_fina_indicator(stock_code))

        revenue = self._ts_num(inc.get("revenue")) if inc is not None else None
        if revenue is None and inc is not None:
            revenue = self._ts_num(inc.get("total_revenue"))
        payload = {
            "report_date": _normalize_report_date(inc.get("end_date")) if inc is not None else None,
            "revenue": revenue,
            "net_profit_parent": self._ts_num(inc.get("n_income_attr_p")) if inc is not None else None,
            "operating_cash_flow": self._ts_num(cf.get("n_cashflow_act")) if cf is not None else None,
            "roe": self._ts_num(fina.get("roe")) if fina is not None else None,
        }
        if all(v is None for v in payload.values()):
            return None
        return payload

    def _tushare_top10_change(self, stock_code: str) -> Optional[float]:
        """十大股东持股变动：取最新报告期 top10_holders 的 hold_change 求和。"""
        fetcher = self._tushare()
        if fetcher is None or not hasattr(fetcher, "get_top10_holders"):
            return None
        df = fetcher.get_top10_holders(stock_code)
        if df is None or getattr(df, "empty", True):
            return None
        if "end_date" not in df.columns or "hold_change" not in df.columns:
            return None
        latest_end = df["end_date"].astype(str).max()
        latest = df[df["end_date"].astype(str) == latest_end]
        changes = pd.to_numeric(latest["hold_change"], errors="coerce").dropna()
        if changes.empty:
            return None
        return float(changes.sum())

    def _call_df_candidates(
        self,
        candidates: List[Tuple[str, Dict[str, Any]]],
    ) -> Tuple[Optional[pd.DataFrame], Optional[str], List[str]]:
        errors: List[str] = []
        try:
            import akshare as ak
        except Exception as exc:
            return None, None, [f"import_akshare:{type(exc).__name__}"]

        for func_name, kwargs in candidates:
            fn = getattr(ak, func_name, None)
            if fn is None:
                continue
            try:
                df = fn(**kwargs)
                if isinstance(df, pd.Series):
                    df = df.to_frame().T
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return df, func_name, errors
            except Exception as exc:
                errors.append(f"{func_name}:{type(exc).__name__}")
                continue
        return None, None, errors

    def get_fundamental_bundle(self, stock_code: str) -> Dict[str, Any]:
        """
        Return normalized fundamental blocks from AkShare with partial tolerance.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "growth": {},
            "earnings": {},
            "institution": {},
            "source_chain": [],
            "errors": [],
        }

        # P1-2：成长 + 财务报表优先取 Tushare（fina_indicator/income/cashflow），
        # 命中则跳过下方 akshare 财务指标块；失败则回退 akshare。
        tushare_growth = self._tushare_growth(stock_code)
        tushare_report = self._tushare_financial_report(stock_code)
        tushare_financials_hit = tushare_growth is not None or tushare_report is not None
        if tushare_growth is not None:
            result["growth"] = tushare_growth
            result["source_chain"].append("growth:tushare:fina_indicator")
        if tushare_report is not None:
            result["earnings"]["financial_report"] = tushare_report
            result["source_chain"].append("financial_report:tushare:income")

        # Financial indicators (akshare 回退，仅当 Tushare 未命中)
        fin_df = None
        if not tushare_financials_hit:
            fin_df, fin_source, fin_errors = self._call_df_candidates([
                ("stock_financial_abstract", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {"symbol": stock_code}),
                ("stock_financial_analysis_indicator", {}),
            ])
            result["errors"].extend(fin_errors)
        if fin_df is not None:
            row = _extract_latest_row(fin_df, stock_code)
            if row is not None:
                revenue_yoy = _safe_float(_pick_by_keywords(row, ["营业收入同比", "营收同比", "收入同比", "同比增长"]))
                profit_yoy = _safe_float(_pick_by_keywords(row, ["净利润同比", "净利同比", "归母净利润同比"]))
                roe = _safe_float(_pick_by_keywords(row, ["净资产收益率", "ROE", "净资产收益"]))
                gross_margin = _safe_float(_pick_by_keywords(row, ["毛利率"]))
                report_date = _normalize_report_date(_pick_by_keywords(row, _DIVIDEND_KEYWORD_MAP["report_date"]))
                revenue = _safe_float(_pick_by_keywords(row, ["营业总收入", "营业收入", "营收"]))
                net_profit_parent = _safe_float(_pick_by_keywords(row, ["归母净利润", "母公司股东净利润", "净利润"]))
                operating_cash_flow = _safe_float(
                    _pick_by_keywords(row, ["经营活动产生的现金流量净额", "经营现金流", "经营活动现金流"])
                )
                result["growth"] = {
                    "revenue_yoy": revenue_yoy,
                    "net_profit_yoy": profit_yoy,
                    "roe": roe,
                    "gross_margin": gross_margin,
                }
                financial_report_payload = {
                    "report_date": report_date,
                    "revenue": revenue,
                    "net_profit_parent": net_profit_parent,
                    "operating_cash_flow": operating_cash_flow,
                    "roe": roe,
                }
                if any(v is not None for v in financial_report_payload.values()):
                    result["earnings"]["financial_report"] = financial_report_payload
                result["source_chain"].append(f"growth:{fin_source}")

        # Earnings forecast
        forecast_df, forecast_source, forecast_errors = self._call_df_candidates([
            ("stock_yjyg_em", {"symbol": stock_code}),
            ("stock_yjyg_em", {}),
            ("stock_yjbb_em", {"symbol": stock_code}),
            ("stock_yjbb_em", {}),
        ])
        result["errors"].extend(forecast_errors)
        if forecast_df is not None:
            row = _extract_latest_row(forecast_df, stock_code)
            if row is not None:
                result["earnings"]["forecast_summary"] = _safe_str(
                    _pick_by_keywords(row, ["预告", "业绩变动", "内容", "摘要", "公告"])
                )[:200]
                result["source_chain"].append(f"earnings_forecast:{forecast_source}")

        # Earnings quick report
        quick_df, quick_source, quick_errors = self._call_df_candidates([
            ("stock_yjkb_em", {"symbol": stock_code}),
            ("stock_yjkb_em", {}),
        ])
        result["errors"].extend(quick_errors)
        if quick_df is not None:
            row = _extract_latest_row(quick_df, stock_code)
            if row is not None:
                result["earnings"]["quick_report_summary"] = _safe_str(
                    _pick_by_keywords(row, ["快报", "摘要", "公告", "说明"])
                )[:200]
                result["source_chain"].append(f"earnings_quick:{quick_source}")

        # Dividend details (cash dividend, pre-tax)
        dividend_df, dividend_source, dividend_errors = self._call_df_candidates([
            ("stock_fhps_detail_em", {"symbol": stock_code}),
            ("stock_history_dividend_detail", {"symbol": stock_code, "indicator": "分红", "date": ""}),
            ("stock_dividend_cninfo", {"symbol": stock_code}),
        ])
        result["errors"].extend(dividend_errors)
        if dividend_df is not None:
            dividend_payload = _build_dividend_payload(dividend_df, stock_code, max_events=5)
            if dividend_payload:
                result["earnings"]["dividend"] = dividend_payload
                result["source_chain"].append(f"dividend:{dividend_source}")

        # Institution / top shareholders
        inst_df, inst_source, inst_errors = self._call_df_candidates([
            ("stock_institute_hold", {}),
            ("stock_institute_recommend", {}),
        ])
        result["errors"].extend(inst_errors)
        if inst_df is not None:
            row = _extract_latest_row(inst_df, stock_code)
            if row is not None:
                inst_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "变动", "持股变化"]))
                result["institution"]["institution_holding_change"] = inst_change
                result["source_chain"].append(f"institution:{inst_source}")

        # P1-2：十大股东持股变动优先取 Tushare top10_holders，失败回退 akshare。
        tushare_top10 = self._tushare_top10_change(stock_code)
        if tushare_top10 is not None:
            result["institution"]["top10_holder_change"] = tushare_top10
            result["source_chain"].append("top10:tushare:top10_holders")
        else:
            top10_df, top10_source, top10_errors = self._call_df_candidates([
                ("stock_gdfx_top_10_em", {"symbol": stock_code}),
                ("stock_gdfx_top_10_em", {}),
                ("stock_zh_a_gdhs_detail_em", {"symbol": stock_code}),
                ("stock_zh_a_gdhs_detail_em", {}),
            ])
            result["errors"].extend(top10_errors)
            if top10_df is not None:
                row = _extract_latest_row(top10_df, stock_code)
                if row is not None:
                    holder_change = _safe_float(_pick_by_keywords(row, ["增减", "变化", "持股变化", "变动"]))
                    result["institution"]["top10_holder_change"] = holder_change
                    result["source_chain"].append(f"top10:{top10_source}")

        has_content = bool(result["growth"] or result["earnings"] or result["institution"])
        result["status"] = "partial" if has_content else "not_supported"
        return result

    def get_capital_flow(self, stock_code: str, top_n: int = 5) -> Dict[str, Any]:
        """
        Return per-stock 主力资金流（仅 Tushare moneyflow，akshare 兜底）。

        说明：原先附带的"板块资金流排名"段写死 akshare、市场级全量扫描耗时约 24s，
        既违背"akshare 末位"铁律、又把 0.3s 就绪的个股资金流一起拖垮，且与独立的
        get_sector_rankings 工具功能重复，故已移除。板块资金流请改用 get_sector_rankings。
        ``top_n`` 仅为兼容旧签名而保留，现已不再使用。
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "stock_flow": {},
            "source_chain": [],
            "errors": [],
        }

        # P1-3：个股资金流优先取 Tushare moneyflow，失败回退 akshare 候选。
        tushare_flow = self._tushare_stock_flow(stock_code)
        if tushare_flow is not None:
            result["stock_flow"] = tushare_flow
            result["source_chain"].append("capital_stock:tushare:moneyflow")
        else:
            stock_df, stock_source, stock_errors = self._call_df_candidates([
                ("stock_individual_fund_flow", {"stock": stock_code}),
                ("stock_individual_fund_flow", {"symbol": stock_code}),
                ("stock_individual_fund_flow", {}),
                ("stock_main_fund_flow", {"symbol": stock_code}),
                ("stock_main_fund_flow", {}),
            ])
            result["errors"].extend(stock_errors)
            if stock_df is not None:
                row = _extract_latest_row(stock_df, stock_code)
                if row is not None:
                    net_inflow = _safe_float(_pick_by_keywords(row, ["主力净流入", "净流入", "净额"]))
                    inflow_5d = _safe_float(_pick_by_keywords(row, ["5日", "五日"]))
                    inflow_10d = _safe_float(_pick_by_keywords(row, ["10日", "十日"]))
                    result["stock_flow"] = {
                        "main_net_inflow": net_inflow,
                        "inflow_5d": inflow_5d,
                        "inflow_10d": inflow_10d,
                    }
                    result["source_chain"].append(f"capital_stock:{stock_source}")

        result["status"] = "partial" if result["stock_flow"] else "not_supported"
        return result

    def get_dragon_tiger_flag(self, stock_code: str, lookback_days: int = 20) -> Dict[str, Any]:
        """
        Return dragon-tiger signal in lookback window.
        """
        result: Dict[str, Any] = {
            "status": "not_supported",
            "is_on_list": False,
            "recent_count": 0,
            "latest_date": None,
            "source_chain": [],
            "errors": [],
        }

        # P1-3：龙虎榜优先取 Tushare top_list（窗口统计），失败回退 akshare。
        tushare_dt = self._tushare_dragon_tiger(stock_code, lookback_days)
        if tushare_dt is not None:
            result.update(tushare_dt)
            result["status"] = "ok"
            result["source_chain"].append("dragon_tiger:tushare:top_list")
            return result

        df, source, errors = self._call_df_candidates([
            ("stock_lhb_stock_statistic_em", {}),
            ("stock_lhb_detail_em", {}),
            ("stock_lhb_jgmmtj_em", {}),
        ])
        result["errors"].extend(errors)
        if df is None:
            return result

        # Try code filter
        code_cols = [c for c in df.columns if any(k in str(c) for k in ("代码", "股票代码", "证券代码"))]
        target = _normalize_code(stock_code)
        matched = pd.DataFrame()
        for col in code_cols:
            try:
                series = df[col].astype(str).map(_normalize_code)
                cur = df[series == target]
                if not cur.empty:
                    matched = cur
                    break
            except Exception:
                continue
        if matched.empty:
            result["source_chain"].append(f"dragon_tiger:{source}")
            result["status"] = "ok" if code_cols else "partial"
            return result

        date_col = next((c for c in matched.columns if any(k in str(c) for k in ("日期", "上榜", "交易日", "time"))), None)
        parsed_dates: List[datetime] = []
        if date_col is not None:
            for val in matched[date_col].astype(str).tolist():
                try:
                    parsed_dates.append(pd.to_datetime(val).to_pydatetime())
                except Exception:
                    continue
        now = datetime.now()
        start = now - timedelta(days=max(1, lookback_days))
        recent_dates = [d for d in parsed_dates if start <= d <= now]

        result["is_on_list"] = bool(recent_dates)
        result["recent_count"] = len(recent_dates) if recent_dates else int(len(matched))
        result["latest_date"] = max(recent_dates).date().isoformat() if recent_dates else (
            max(parsed_dates).date().isoformat() if parsed_dates else None
        )
        result["status"] = "ok"
        result["source_chain"].append(f"dragon_tiger:{source}")
        return result
