"""Generic A-share market history with deterministic CSV caching."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
import json
import logging
import os
from pathlib import Path
from typing import Literal

import baostock as bs

from .bank_base import _BAOSTOCK_LOCK
from .industry_catalog import IndustryStockProfile


logger = logging.getLogger("bank_valuation.strategy_history")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = Path(os.getenv("STRATEGY_CACHE_DIR", str(_PROJECT_ROOT / "output" / "strategy_cache")))
Adjustment = Literal["raw", "post"]


@dataclass(frozen=True)
class SecurityMarketPoint:
    close: float
    pb: float | None
    pe: float | None


@dataclass(frozen=True)
class MarketHistoryLoad:
    histories: dict[str, dict[date, SecurityMarketPoint]]
    failures: list[dict[str, str]]


def _cache_path(code: str, adjustment: Adjustment) -> Path:
    return _CACHE_DIR / f"{code.replace('.', '_')}_{adjustment}_history.csv"


def _metadata_path(code: str, adjustment: Adjustment) -> Path:
    return _CACHE_DIR / f"{code.replace('.', '_')}_{adjustment}_history.meta.json"


def _read_metadata(code: str, adjustment: Adjustment) -> tuple[date, date] | None:
    path = _metadata_path(code, adjustment)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return date.fromisoformat(payload["requested_start"]), date.fromisoformat(payload["requested_end"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _optional_positive(value: str | None) -> float | None:
    try:
        parsed = float(value or "")
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _read_cache(code: str, adjustment: Adjustment) -> dict[date, SecurityMarketPoint]:
    path = _cache_path(code, adjustment)
    if not path.exists():
        return {}
    output: dict[date, SecurityMarketPoint] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                close = _optional_positive(row.get("close"))
                if close is None:
                    continue
                output[date.fromisoformat(row["date"])] = SecurityMarketPoint(
                    close=close,
                    pb=_optional_positive(row.get("pb")),
                    pe=_optional_positive(row.get("pe")),
                )
    except (OSError, KeyError, ValueError):
        logger.warning("Strategy history cache unreadable: path=%s", path, exc_info=True)
        return {}
    return output


def _write_cache(
    code: str,
    adjustment: Adjustment,
    history: dict[date, SecurityMarketPoint],
    requested_start: date,
    requested_end: date,
) -> None:
    path = _cache_path(code, adjustment)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["date", "close", "pb", "pe"])
        writer.writeheader()
        writer.writerows(
            {
                "date": day.isoformat(),
                "close": point.close,
                "pb": "" if point.pb is None else point.pb,
                "pe": "" if point.pe is None else point.pe,
            }
            for day, point in sorted(history.items())
        )
    temporary.replace(path)
    metadata_path = _metadata_path(code, adjustment)
    metadata_temporary = metadata_path.with_suffix(".tmp")
    metadata_temporary.write_text(
        json.dumps(
            {
                "requested_start": requested_start.isoformat(),
                "requested_end": requested_end.isoformat(),
                "first_data_date": min(history).isoformat(),
                "last_data_date": max(history).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    metadata_temporary.replace(metadata_path)


def _cache_covers(
    code: str,
    adjustment: Adjustment,
    history: dict[date, SecurityMarketPoint],
    start_date: date,
    end_date: date,
) -> bool:
    if not history:
        return False
    covered = _read_metadata(code, adjustment)
    return bool(covered and covered[0] <= start_date and covered[1] >= end_date)


def _query_history(
    code: str,
    start_date: date,
    end_date: date,
    adjustment: Adjustment,
) -> dict[date, SecurityMarketPoint]:
    adjustflag = "3" if adjustment == "raw" else "1"
    fields = ["date", "close", "pbMRQ", "peTTM", "tradestatus"]
    query = bs.query_history_k_data_plus(
        code,
        ",".join(fields),
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        frequency="d",
        adjustflag=adjustflag,
    )
    if query.error_code != "0":
        raise RuntimeError(f"Baostock 查询失败: {query.error_msg}")
    output: dict[date, SecurityMarketPoint] = {}
    while query.next():
        row = dict(zip(fields, query.get_row_data()))
        close = _optional_positive(row.get("close"))
        if close is None or row.get("tradestatus") not in {None, "", "1"}:
            continue
        output[date.fromisoformat(row["date"])] = SecurityMarketPoint(
            close=close,
            pb=_optional_positive(row.get("pbMRQ")),
            pe=_optional_positive(row.get("peTTM")),
        )
    if not output:
        raise RuntimeError("指定日期区间没有可用日线")
    return output


def load_market_histories(
    stocks: list[IndustryStockProfile],
    start_date: date,
    end_date: date,
    *,
    adjustment: Adjustment = "post",
    refresh_cache: bool = False,
) -> MarketHistoryLoad:
    """Load selected histories, sharing one serialized Baostock session.

    ``post`` uses post-adjusted closes for strategy returns so cash dividends,
    splits and bonus shares are represented once. ``raw`` keeps the traded
    close and is used for current valuation analysis.
    """
    histories: dict[str, dict[date, SecurityMarketPoint]] = {}
    failures: list[dict[str, str]] = []
    missing: list[tuple[IndustryStockProfile, date, date]] = []
    for stock in stocks:
        cached = _read_cache(stock.code, adjustment)
        if not refresh_cache and _cache_covers(stock.code, adjustment, cached, start_date, end_date):
            histories[stock.code] = {
                day: point for day, point in cached.items() if start_date <= day <= end_date
            }
        else:
            covered = _read_metadata(stock.code, adjustment)
            query_start = min(start_date, covered[0]) if covered else start_date
            query_end = max(end_date, covered[1]) if covered else end_date
            missing.append((stock, query_start, query_end))

    if not missing:
        return MarketHistoryLoad(histories=histories, failures=failures)

    with _BAOSTOCK_LOCK:
        login = bs.login()
        if login.error_code != "0":
            message = f"Baostock 登录失败: {login.error_msg}"
            for stock, _, _ in missing:
                failures.append({"stock_code": stock.code, "error": message})
            return MarketHistoryLoad(histories=histories, failures=failures)
        try:
            queries_in_session = 0
            for stock, query_start, query_end in missing:
                try:
                    # The public Baostock session can expire after several large
                    # history queries. Rotate it in small deterministic batches.
                    if queries_in_session >= 4:
                        try:
                            bs.logout()
                        except Exception:
                            logger.debug("Baostock session already closed during rotation", exc_info=True)
                        relogin = bs.login()
                        if relogin.error_code != "0":
                            raise RuntimeError(f"Baostock 重新登录失败: {relogin.error_msg}")
                        queries_in_session = 0
                    try:
                        loaded = _query_history(stock.code, query_start, query_end, adjustment)
                    except RuntimeError as exc:
                        if "未登录" not in str(exc):
                            raise
                        relogin = bs.login()
                        if relogin.error_code != "0":
                            raise RuntimeError(f"Baostock 重新登录失败: {relogin.error_msg}") from exc
                        queries_in_session = 0
                        loaded = _query_history(stock.code, query_start, query_end, adjustment)
                    queries_in_session += 1
                    _write_cache(stock.code, adjustment, loaded, query_start, query_end)
                    histories[stock.code] = {
                        day: point for day, point in loaded.items() if start_date <= day <= end_date
                    }
                    logger.info(
                        "Strategy history loaded: code=%s adjustment=%s rows=%d range=%s..%s",
                        stock.code,
                        adjustment,
                        len(loaded),
                        min(loaded),
                        max(loaded),
                    )
                except Exception as exc:
                    cached = _read_cache(stock.code, adjustment)
                    usable = {
                        day: point for day, point in cached.items() if start_date <= day <= end_date
                    }
                    if usable:
                        histories[stock.code] = usable
                    failures.append({"stock_code": stock.code, "error": str(exc)})
                    logger.warning("Strategy history failed: code=%s", stock.code, exc_info=True)
        finally:
            try:
                bs.logout()
            except Exception:
                logger.warning("Baostock logout failed after strategy history load", exc_info=True)
    return MarketHistoryLoad(histories=histories, failures=failures)
