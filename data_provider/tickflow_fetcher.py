# -*- coding: utf-8 -*-
"""
===================================
TickFlowFetcher
===================================

TickFlow 数据源适配器，覆盖：

1. A 股日K（进入 DataFetcherManager 的 fetcher 链，作为 A 股日线主力，priority=-2）
2. A 股实时报价（get_realtime_quote，作为实时主力，由 base.py 配置字符串路由调度）
3. 主要 A 股指数行情 / A 股市场宽度统计（大盘复盘，由 manager 显式调用）

实时报价虽走 base.py 的配置字符串路由（realtime_source_priority 含 tickflow），但 base 仍以
_get_fetcher_by_name(..., capability="realtime_quote") 解析本实例，故能力探针须放行 realtime_quote。
"""

import logging
import math
import os
from datetime import datetime
from threading import RLock
from time import monotonic
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import (
    STANDARD_COLUMNS,
    BaseFetcher,
    DataFetchError,
    is_bse_code,
    is_kc_cy_stock,
    is_st_stock,
    normalize_stock_code,
    to_exchange_suffixed_code,
)


logger = logging.getLogger(__name__)

_CN_MAIN_INDEX_QUOTES = (
    ("000001.SH", "000001", "上证指数"),
    ("399001.SZ", "399001", "深证成指"),
    ("399006.SZ", "399006", "创业板指"),
    ("000688.SH", "000688", "科创50"),
    ("000016.SH", "000016", "上证50"),
    ("000300.SH", "000300", "沪深300"),
)
_MAX_SYMBOLS_PER_QUOTE_REQUEST = 5
_UNIVERSE_PERMISSION_NEGATIVE_CACHE_TTL_SECONDS = 900

# 日K 取数参数
_DAILY_PERIOD = "1d"          # TickFlow klines period
_DAILY_ADJUST = "forward"     # 前复权（TickFlow 合法值：forward/backward/none 等，非 qfq/hfq）
_MS_PER_DAY = 86_400_000
# 分钟K 合法周期（TickFlow klines.get period）
_INTRADAY_PERIODS = ("5m", "15m", "30m", "60m")
# 分钟K/五档网络调用最大尝试次数（W5）：连续失败达此次数后降级返回 None
_INTRADAY_MAX_ATTEMPTS = 2
# 分钟K 标准输出列（新增能力，无历史契约；含 datetime 取自 trade_time）
_INTRADAY_COLUMNS = ["code", "datetime", "open", "high", "low", "close", "volume", "amount"]
# TickFlow 进链后承担的能力（A 股日线 + 实时报价 + P2 分钟K/五档）。
# 注：实时报价虽走 base.py 配置字符串路由，但 base 仍以
# _get_fetcher_by_name(..., capability="realtime_quote") 经此探针解析实例，故须放行。
# 分钟K/五档为 TickFlow 专属新增能力，base 同样经 _get_fetcher_by_name 解析，故须放行。
_SUPPORTED_CAPABILITIES = {
    "daily_data",
    "daily",
    "realtime_quote",
    "intraday_kline",
    "order_book",
    "",
}


class TickFlowFetcher(BaseFetcher):
    """TickFlow 数据源：A 股日K（进链主力）+ 大盘复盘指数/宽度。"""

    name = "TickFlowFetcher"
    # 静态类属性、import 时求值（与其它 fetcher 一致）；token 有效时才会被实例化进链。
    priority = int(os.getenv("TICKFLOW_PRIORITY", "-2"))

    def __init__(self, api_key: Optional[str], timeout: float = 30.0):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self._client = None
        self._client_lock = RLock()
        self._universe_query_supported: Optional[bool] = None
        self._universe_query_checked_at: Optional[float] = None

    def close(self) -> None:
        """Close the underlying TickFlow client if it was created."""
        with self._client_lock:
            client = self._client
            self._client = None
            self._universe_query_supported = None
            self._universe_query_checked_at = None
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                logger.debug("[TickFlowFetcher] 关闭客户端失败: %s", exc)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Best-effort cleanup during interpreter shutdown.
            pass

    def _build_client(self):
        from tickflow import TickFlow

        return TickFlow(api_key=self.api_key, timeout=self.timeout)

    def _get_client(self):
        if not self.api_key:
            return None
        if self._client is not None:
            return self._client

        with self._client_lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    def is_available_for_request(self, capability: str = "") -> bool:
        """能力探针：配置了 api_key 且能力属于本源承担范围（A 股日线）时可用。"""
        if not self.api_key:
            return False
        return capability in _SUPPORTED_CAPABILITIES

    @staticmethod
    def _date_to_ms(date_str: str, *, end_of_day: bool = False) -> int:
        """'YYYY-MM-DD' → epoch 毫秒。end_of_day=True 时取当日 23:59:59.999 以含整日。"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        ms = int(dt.timestamp() * 1000)
        if end_of_day:
            ms += _MS_PER_DAY - 1
        return ms

    def _fetch_raw_data(
        self, stock_code: str, start_date: str, end_date: str
    ) -> pd.DataFrame:
        """通过 TickFlow klines.get 获取 A 股日K 原始数据。"""
        symbol = to_exchange_suffixed_code(stock_code)
        if not symbol:
            raise DataFetchError(
                f"TickFlowFetcher 仅支持 A 股，无法处理代码 {stock_code}"
            )

        client = self._get_client()
        if client is None:
            raise DataFetchError("TickFlowFetcher 未配置 api_key，数据源不可用")

        df = client.klines.get(
            symbol,
            period=_DAILY_PERIOD,
            start_time=self._date_to_ms(start_date),
            end_time=self._date_to_ms(end_date, end_of_day=True),
            adjust=_DAILY_ADJUST,
            as_dataframe=True,
        )
        if df is None or (hasattr(df, "empty") and df.empty):
            raise DataFetchError(f"TickFlowFetcher 未返回 {symbol} 的日K数据")
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        """将 TickFlow 日K 列映射为项目标准列：

        ``['code','date','open','high','low','close','volume','amount','pct_chg']``。
        TickFlow 不提供 pct_chg（涨跌幅%），此处用收盘价环比自算（首行无前值置 None）。
        """
        code = normalize_stock_code(stock_code)
        # 按交易日升序，保证 pct_chg 环比方向正确（TickFlow 通常已升序，这里再保险一次）
        src = df.sort_values("trade_date").reset_index(drop=True)

        out = pd.DataFrame()
        out["code"] = [code] * len(src)
        out["date"] = src["trade_date"]
        for col in ("open", "high", "low", "close", "volume", "amount"):
            out[col] = pd.to_numeric(src[col], errors="coerce")
        # 涨跌幅%：(close_t - close_{t-1}) / close_{t-1} * 100，首行为 NaN
        out["pct_chg"] = out["close"].pct_change() * 100.0

        return out[["code"] + STANDARD_COLUMNS]

    def get_realtime_quote(self, stock_code: str):
        """获取 A 股实时报价（TickFlow ``quotes.get``）。

        - 非 A 股代码（美股/港股）返回 ``None``——TickFlow 在本项目仅承担 A 股。
        - 映射为 ``UnifiedRealtimeQuote``。``ext`` 内 ``change_pct``/``turnover_rate``/
          ``amplitude`` 为**比率**，转成百分比（与 ``get_main_indices`` 同口径）。
        - PE/PB/市值/量比 TickFlow 不提供，置 ``None``，由 base.py 从后续源补充。
        """
        from .realtime_types import RealtimeSource, UnifiedRealtimeQuote, safe_int

        symbol = to_exchange_suffixed_code(stock_code)
        if not symbol:
            return None

        client = self._get_client()
        if client is None:
            return None

        quotes = client.quotes.get(symbols=[symbol])
        if not quotes:
            return None

        quote = quotes[0]
        if not quote:
            return None

        ext = quote.get("ext") or {}
        return UnifiedRealtimeQuote(
            code=normalize_stock_code(stock_code),
            name=self._extract_name(quote),
            source=RealtimeSource.TICKFLOW,
            price=self._safe_float(quote.get("last_price")),
            change_pct=self._ratio_to_percent(ext.get("change_pct")),
            change_amount=self._safe_float(ext.get("change_amount")),
            volume=safe_int(quote.get("volume")),
            amount=self._safe_float(quote.get("amount")),
            turnover_rate=self._ratio_to_percent(ext.get("turnover_rate")),
            amplitude=self._ratio_to_percent(ext.get("amplitude")),
            open_price=self._safe_float(quote.get("open")),
            high=self._safe_float(quote.get("high")),
            low=self._safe_float(quote.get("low")),
            pre_close=self._safe_float(quote.get("prev_close")),
        )

    @staticmethod
    def _call_with_intraday_retry(label: str, func: Any) -> Any:
        """分钟K/五档网络调用的轻量重试（W5）。

        连续失败 ``_INTRADAY_MAX_ATTEMPTS`` 次后降级返回 None（不向上抛异常拖垮
        manager 层），并记录可观测日志区分限流/瞬时失败与无数据。func 正常返回的
        None（空数据）与降级返回的 None 对调用方等价，均按"无数据"处理。
        """
        for attempt in range(1, _INTRADAY_MAX_ATTEMPTS + 1):
            try:
                return func()
            except Exception as exc:
                logger.warning(
                    "[TickFlowFetcher] %s 第 %d/%d 次调用失败: %s",
                    label,
                    attempt,
                    _INTRADAY_MAX_ATTEMPTS,
                    exc,
                )
        logger.warning(
            "[TickFlowFetcher] %s 连续 %d 次失败，降级返回 None",
            label,
            _INTRADAY_MAX_ATTEMPTS,
        )
        return None

    def get_intraday_kline(
        self, stock_code: str, period: str = "5m", count: int = 240
    ) -> Optional[pd.DataFrame]:
        """获取 A 股分钟K线（TickFlow ``klines.get``）。

        - ``period`` 合法值：``5m``/``15m``/``30m``/``60m``，其它抛 ``ValueError``。
        - 非 A 股代码返回 ``None``（TickFlow 在本项目仅承担 A 股）。
        - 输出标准列 ``_INTRADAY_COLUMNS``（``datetime`` 取自 TickFlow ``trade_time``）。
        - 空数据/未配置 key 返回 ``None``。
        """
        if period not in _INTRADAY_PERIODS:
            raise ValueError(
                f"不支持的分钟K周期 {period!r}，合法值：{_INTRADAY_PERIODS}"
            )

        symbol = to_exchange_suffixed_code(stock_code)
        if not symbol:
            return None

        client = self._get_client()
        if client is None:
            return None

        df = self._call_with_intraday_retry(
            f"分钟K {symbol} {period}",
            lambda: client.klines.get(
                symbol,
                period=period,
                count=count,
                adjust=_DAILY_ADJUST,
                as_dataframe=True,
            ),
        )
        if df is None or (hasattr(df, "empty") and df.empty):
            return None

        code = normalize_stock_code(stock_code)
        out = pd.DataFrame()
        out["code"] = [code] * len(df)
        out["datetime"] = df["trade_time"].astype(str).values
        for col in ("open", "high", "low", "close", "volume", "amount"):
            out[col] = pd.to_numeric(df[col], errors="coerce").values
        return out[_INTRADAY_COLUMNS]

    def get_order_book(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """获取 A 股五档盘口（TickFlow ``depth.get``）。

        - 非 A 股代码返回 ``None``。
        - 归一为 ``{"code", "timestamp", "bids": [{"price","volume"}...], "asks": [...]}``，
          买/卖各五档（买一→买五、卖一→卖五）。
        - 空数据/未配置 key 返回 ``None``。
        """
        symbol = to_exchange_suffixed_code(stock_code)
        if not symbol:
            return None

        client = self._get_client()
        if client is None:
            return None

        depth = self._call_with_intraday_retry(
            f"五档 {symbol}", lambda: client.depth.get(symbol)
        )
        if not depth or not isinstance(depth, dict):
            return None

        bids = self._pair_price_volume(
            depth.get("bid_prices"), depth.get("bid_volumes")
        )
        asks = self._pair_price_volume(
            depth.get("ask_prices"), depth.get("ask_volumes")
        )
        if not bids and not asks:
            return None

        return {
            "code": normalize_stock_code(stock_code),
            "timestamp": depth.get("timestamp"),
            "bids": bids,
            "asks": asks,
        }

    @classmethod
    def _pair_price_volume(
        cls, prices: Any, volumes: Any
    ) -> List[Dict[str, Any]]:
        """把 TickFlow 平行的 prices/volumes 列表配成 [{price, volume}...]。"""
        if not prices or not volumes:
            return []
        levels: List[Dict[str, Any]] = []
        for price, volume in zip(prices, volumes):
            levels.append({"price": cls._safe_float(price), "volume": volume})
        return levels

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value in (None, "", "-"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _ratio_to_percent(cls, value: Any) -> Optional[float]:
        ratio = cls._safe_float(value)
        if ratio is None:
            return None
        return ratio * 100.0

    @staticmethod
    def _extract_name(quote: Dict[str, Any]) -> str:
        ext = quote.get("ext") or {}
        name = ext.get("name") or quote.get("name") or ""
        return str(name).strip()

    @staticmethod
    def _is_universe_permission_error(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        code = str(getattr(exc, "code", "") or "").upper()
        message = (
            f"{getattr(exc, 'message', '')} {exc}"
        ).strip().lower()

        if status_code == 403:
            return True
        if code in {"PERMISSION_DENIED", "FORBIDDEN"}:
            return True
        return any(
            keyword in message
            for keyword in (
                "标的池查询",
                "universe",
                "permission",
                "forbidden",
            )
        )

    @staticmethod
    def _is_cn_equity_symbol(symbol: str) -> bool:
        normalized = normalize_stock_code(symbol)
        upper_symbol = (symbol or "").strip().upper()
        return (
            normalized.isdigit()
            and len(normalized) == 6
            and upper_symbol.endswith((".SH", ".SZ", ".BJ"))
        )

    @staticmethod
    def _round_limit_price(prev_close: float, ratio: float) -> float:
        return math.floor(prev_close * (1 + ratio) * 100 + 0.5) / 100.0

    @classmethod
    def _get_limit_ratio(cls, pure_code: str, name: str) -> float:
        if is_bse_code(pure_code):
            return 0.30
        if is_kc_cy_stock(pure_code):
            return 0.20
        if is_st_stock(name):
            return 0.05
        return 0.10

    def get_main_indices(self, region: str = "cn") -> Optional[List[Dict[str, Any]]]:
        """Fetch main A-share indices via TickFlow quotes."""
        if region != "cn":
            return None

        client = self._get_client()
        if client is None:
            return None

        symbols = [symbol for symbol, _, _ in _CN_MAIN_INDEX_QUOTES]
        quotes: List[Dict[str, Any]] = []
        for offset in range(0, len(symbols), _MAX_SYMBOLS_PER_QUOTE_REQUEST):
            batch_symbols = symbols[offset : offset + _MAX_SYMBOLS_PER_QUOTE_REQUEST]
            batch_quotes = client.quotes.get(symbols=batch_symbols)
            if batch_quotes:
                quotes.extend(batch_quotes)
        if not quotes:
            logger.warning("[TickFlowFetcher] 指数行情为空")
            return None

        quotes_by_symbol = {
            str(item.get("symbol", "")).upper(): item for item in quotes if item
        }
        results: List[Dict[str, Any]] = []

        for symbol, code, name in _CN_MAIN_INDEX_QUOTES:
            quote = quotes_by_symbol.get(symbol)
            if not quote:
                continue

            ext = quote.get("ext") or {}
            current = self._safe_float(quote.get("last_price")) or 0.0
            prev_close = self._safe_float(quote.get("prev_close")) or 0.0
            change = self._safe_float(ext.get("change_amount"))
            if change is None:
                change = current - prev_close if current or prev_close else 0.0
            amplitude = self._ratio_to_percent(ext.get("amplitude"))
            if amplitude is None and prev_close > 0:
                high = self._safe_float(quote.get("high")) or 0.0
                low = self._safe_float(quote.get("low")) or 0.0
                amplitude = (high - low) / prev_close * 100

            results.append(
                {
                    "code": code,
                    "name": name,
                    "current": current,
                    "change": change,
                    "change_pct": self._ratio_to_percent(ext.get("change_pct")) or 0.0,
                    "open": self._safe_float(quote.get("open")) or 0.0,
                    "high": self._safe_float(quote.get("high")) or 0.0,
                    "low": self._safe_float(quote.get("low")) or 0.0,
                    "prev_close": prev_close,
                    "volume": self._safe_float(quote.get("volume")) or 0.0,
                    "amount": self._safe_float(quote.get("amount")) or 0.0,
                    "amplitude": amplitude or 0.0,
                }
            )

        if len(results) != len(_CN_MAIN_INDEX_QUOTES):
            logger.warning(
                "[TickFlowFetcher] 指数行情不完整: %s/%s",
                len(results),
                len(_CN_MAIN_INDEX_QUOTES),
            )
            return None

        return results or None

    def _query_a_share_universe(self) -> Optional[List[Dict[str, Any]]]:
        """查询 A 股全市场 universe 行情（CN_Equity_A），带标的池权限负缓存。

        无 client / 无标的池权限 / 空结果时返回 None；
        供市场宽度统计（get_market_stats）与自建涨停池（get_limit_up_pool）复用。
        """
        client = self._get_client()
        if client is None:
            return None

        now = monotonic()
        if self._universe_query_supported is False:
            checked_at = self._universe_query_checked_at or 0.0
            if (
                now - checked_at
                < _UNIVERSE_PERMISSION_NEGATIVE_CACHE_TTL_SECONDS
            ):
                return None
            self._universe_query_supported = None
            self._universe_query_checked_at = None

        try:
            quotes = client.quotes.get(universes=["CN_Equity_A"])
            self._universe_query_supported = True
            self._universe_query_checked_at = now
        except Exception as exc:
            if self._is_universe_permission_error(exc):
                self._universe_query_supported = False
                self._universe_query_checked_at = now
                logger.info(
                    "[TickFlowFetcher] 当前套餐不支持标的池查询，回退到现有数据源"
                )
                return None
            raise
        if not quotes:
            return None
        return quotes

    def get_market_stats(self) -> Optional[Dict[str, Any]]:
        """Calculate A-share market breadth from TickFlow universe quotes."""
        quotes = self._query_a_share_universe()
        if not quotes:
            logger.warning("[TickFlowFetcher] 市场统计行情为空或标的池不可用")
            return None

        stats = {
            "up_count": 0,
            "down_count": 0,
            "flat_count": 0,
            "limit_up_count": 0,
            "limit_down_count": 0,
            "total_amount": 0.0,
        }
        valid_rows = 0

        for quote in quotes:
            if not quote:
                continue

            symbol = str(quote.get("symbol") or "").strip().upper()
            if not self._is_cn_equity_symbol(symbol):
                continue

            amount = self._safe_float(quote.get("amount"))
            if amount is not None and amount > 0:
                stats["total_amount"] += amount / 1e8

            pure_code = normalize_stock_code(symbol)
            last_price = self._safe_float(quote.get("last_price"))
            prev_close = self._safe_float(quote.get("prev_close"))

            if last_price is None or prev_close is None or amount is None or amount <= 0:
                continue

            name = self._extract_name(quote)
            if not name:
                logger.debug("[TickFlowFetcher] 缺少股票名称，按非 ST 处理: %s", symbol)

            ratio = self._get_limit_ratio(pure_code, name)
            limit_up = self._round_limit_price(prev_close, ratio)
            limit_down = math.floor(prev_close * (1 - ratio) * 100 + 0.5) / 100.0
            limit_up_tolerance = round(abs(prev_close * (1 + ratio) - limit_up), 10)
            limit_down_tolerance = round(
                abs(prev_close * (1 - ratio) - limit_down), 10
            )

            valid_rows += 1

            if abs(last_price - limit_up) <= limit_up_tolerance:
                stats["limit_up_count"] += 1
            if abs(last_price - limit_down) <= limit_down_tolerance:
                stats["limit_down_count"] += 1

            if last_price > prev_close:
                stats["up_count"] += 1
            elif last_price < prev_close:
                stats["down_count"] += 1
            else:
                stats["flat_count"] += 1

        if valid_rows == 0:
            logger.warning("[TickFlowFetcher] 市场统计未命中有效 A 股行情")
            return None

        return stats

    def get_limit_up_pool(
        self, date: Optional[str] = None, n: int = 20
    ) -> Optional[List[Dict[str, Any]]]:
        """自建 A 股涨停池：universe 行情 + 计算涨停价，last_price 触及涨停价入池。

        涨停价判定复用 ``_get_limit_ratio``/``_round_limit_price``（与 get_market_stats
        同口径，已覆盖主板/科创创业/北交所/ST 不同涨跌幅）。按成交额降序、截断 ``n``。
        进链 priority=-2，自然先于 akshare 兜底命中（base.py get_limit_up_pool 遍历链）。
        无标的池权限/空/无涨停股 → None，回退后续数据源。``date`` 为 universe 实时盘口
        快照，仅支持当日：传入非当日（历史/未来）日期时返回 None，让 base 遍历链回退到
        可查历史的后续数据源，避免把当日快照误当成历史涨停池（兼容 YYYYMMDD / YYYY-MM-DD）。
        """
        if date:
            normalized = str(date).replace("-", "").strip()[:8]
            if normalized != datetime.now().strftime("%Y%m%d"):
                logger.info(
                    "[TickFlowFetcher] 涨停池仅支持当日实时快照，"
                    "请求日期 %s 非当日 → 返回 None 回退后续源",
                    date,
                )
                return None

        quotes = self._query_a_share_universe()
        if not quotes:
            return None

        pool: List[Dict[str, Any]] = []
        for quote in quotes:
            if not quote:
                continue
            symbol = str(quote.get("symbol") or "").strip().upper()
            if not self._is_cn_equity_symbol(symbol):
                continue

            last_price = self._safe_float(quote.get("last_price"))
            prev_close = self._safe_float(quote.get("prev_close"))
            if last_price is None or prev_close is None or prev_close <= 0:
                continue

            pure_code = normalize_stock_code(symbol)
            name = self._extract_name(quote)
            ratio = self._get_limit_ratio(pure_code, name)
            limit_up = self._round_limit_price(prev_close, ratio)
            tolerance = round(abs(prev_close * (1 + ratio) - limit_up), 10)
            if abs(last_price - limit_up) > tolerance:
                continue

            ext = quote.get("ext") or {}
            pool.append(
                {
                    "code": pure_code,
                    "name": name,
                    "price": last_price,
                    "change_pct": self._ratio_to_percent(ext.get("change_pct")),
                    "amount": self._safe_float(quote.get("amount")),
                    "limit_up": limit_up,
                }
            )

        if not pool:
            return None
        pool.sort(key=lambda item: (item.get("amount") or 0.0), reverse=True)
        return pool[:n]
