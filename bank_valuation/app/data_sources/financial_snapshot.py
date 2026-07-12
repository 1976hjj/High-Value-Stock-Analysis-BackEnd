"""Point-in-time Baostock financial snapshots for industry automation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import baostock as bs

from .bank_base import _BAOSTOCK_LOCK
from .industry_catalog import IndustryStockProfile


logger = logging.getLogger("bank_valuation.financial_snapshot")
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = Path(os.getenv("FINANCIAL_CACHE_DIR", str(_PROJECT_ROOT / "output" / "financial_cache")))


@dataclass(frozen=True)
class FinancialSnapshot:
    code: str
    report_date: date
    published_date: date
    fiscal_year: int
    fiscal_quarter: int
    values: dict[str, float | None]
    cash_dividend_ttm: float


def _cache_path(code: str, valuation_date: date) -> Path:
    return _CACHE_DIR / f"{code.replace('.', '_')}_{valuation_date.isoformat()}.json"


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _read_cache(code: str, valuation_date: date) -> FinancialSnapshot | None:
    path = _cache_path(code, valuation_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if datetime.now() - fetched_at > timedelta(hours=24):
            return None
        snapshot = payload["snapshot"]
        return FinancialSnapshot(
            code=snapshot["code"],
            report_date=date.fromisoformat(snapshot["report_date"]),
            published_date=date.fromisoformat(snapshot["published_date"]),
            fiscal_year=int(snapshot["fiscal_year"]),
            fiscal_quarter=int(snapshot["fiscal_quarter"]),
            values={key: _number(value) for key, value in snapshot["values"].items()},
            cash_dividend_ttm=float(snapshot["cash_dividend_ttm"]),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Financial cache unreadable: path=%s", path, exc_info=True)
        return None


def _write_cache(snapshot: FinancialSnapshot, valuation_date: date) -> None:
    path = _cache_path(snapshot.code, valuation_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    payload = asdict(snapshot)
    payload["report_date"] = snapshot.report_date.isoformat()
    payload["published_date"] = snapshot.published_date.isoformat()
    temporary.write_text(
        json.dumps({"fetched_at": datetime.now().isoformat(), "snapshot": payload}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _rows(result: Any) -> list[dict[str, str]]:
    if result.error_code != "0":
        raise RuntimeError(f"Baostock 财务查询失败: {result.error_msg}")
    output: list[dict[str, str]] = []
    while result.next():
        output.append(dict(zip(result.fields, result.get_row_data())))
    return output


def _latest_published(rows: list[dict[str, str]], valuation_date: date) -> dict[str, str] | None:
    usable = []
    for row in rows:
        try:
            published = date.fromisoformat(row["pubDate"])
            statement = date.fromisoformat(row["statDate"])
        except (KeyError, TypeError, ValueError):
            continue
        if published <= valuation_date and statement <= valuation_date:
            usable.append((statement, published, row))
    return max(usable, default=(None, None, None), key=lambda item: (item[0], item[1]))[2]


def _candidate_periods(valuation_date: date) -> list[tuple[int, int]]:
    periods: list[tuple[date, int, int]] = []
    quarter_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    for year in range(valuation_date.year, valuation_date.year - 4, -1):
        for quarter, (month, day) in quarter_ends.items():
            statement_date = date(year, month, day)
            if statement_date <= valuation_date:
                periods.append((statement_date, year, quarter))
    return [(year, quarter) for _, year, quarter in sorted(periods, reverse=True)]


def _revenue(row: dict[str, str] | None) -> float | None:
    if not row:
        return None
    reported = _number(row.get("MBRevenue"))
    if reported is not None and reported > 0:
        return reported
    net_profit = _number(row.get("netProfit"))
    net_margin = _number(row.get("npMargin"))
    if net_profit is None or net_margin is None or net_margin == 0:
        return None
    return net_profit / net_margin


def _query_dividend_ttm(code: str, valuation_date: date) -> float:
    start = valuation_date - timedelta(days=365)
    total = 0.0
    seen: set[tuple[str, float]] = set()
    for year in range(valuation_date.year, valuation_date.year - 3, -1):
        for row in _rows(bs.query_dividend_data(code, year=year, yearType="report")):
            cash = _number(row.get("dividCashPsBeforeTax"))
            raw_date = row.get("dividOperateDate") or row.get("dividPayDate") or row.get("dividPlanDate")
            if cash is None or cash <= 0 or not raw_date:
                continue
            try:
                effective_date = date.fromisoformat(raw_date)
            except ValueError:
                continue
            marker = (effective_date.isoformat(), cash)
            if start < effective_date <= valuation_date and marker not in seen:
                total += cash
                seen.add(marker)
    return total


_DATASETS: dict[str, Callable[..., Any]] = {
    "operation": bs.query_operation_data,
    "growth": bs.query_growth_data,
    "balance": bs.query_balance_data,
    "cash": bs.query_cash_flow_data,
    "dupont": bs.query_dupont_data,
}


def _fetch_snapshot(stock: IndustryStockProfile, valuation_date: date) -> FinancialSnapshot:
    selected: tuple[int, int, dict[str, str]] | None = None
    for year, quarter in _candidate_periods(valuation_date):
        row = _latest_published(_rows(bs.query_profit_data(stock.code, year=year, quarter=quarter)), valuation_date)
        if row:
            selected = (year, quarter, row)
            break
    if selected is None:
        raise RuntimeError(f"{stock.name} 在估值日前没有可用财务报告")
    year, quarter, profit = selected
    dataset_rows: dict[str, dict[str, str]] = {"profit": profit}
    for name, query in _DATASETS.items():
        row = _latest_published(_rows(query(stock.code, year=year, quarter=quarter)), valuation_date)
        if row:
            dataset_rows[name] = row
    previous = _latest_published(
        _rows(bs.query_profit_data(stock.code, year=year - 1, quarter=quarter)),
        valuation_date,
    )

    values: dict[str, float | None] = {}
    for row in dataset_rows.values():
        for key, value in row.items():
            if key not in {"code", "pubDate", "statDate"}:
                values[key] = _number(value)
    revenue = _revenue(profit)
    previous_revenue = _revenue(previous)
    previous_profit = _number(previous.get("netProfit")) if previous else None
    net_profit = _number(profit.get("netProfit"))
    values["revenue"] = revenue
    values["revenue_yoy"] = (
        revenue / previous_revenue - 1
        if revenue is not None and previous_revenue not in {None, 0}
        else None
    )
    values["net_profit_yoy_calculated"] = (
        net_profit / previous_profit - 1
        if net_profit is not None and previous_profit not in {None, 0}
        else None
    )
    annualization = 4 / quarter
    values["roe_annualized"] = (values.get("roeAvg") or values.get("dupontROE"))
    if values["roe_annualized"] is not None:
        values["roe_annualized"] *= annualization
    cfo_to_np = values.get("CFOToNP")
    values["cfo_annualized"] = (
        net_profit * cfo_to_np * annualization
        if net_profit is not None and cfo_to_np is not None
        else None
    )
    return FinancialSnapshot(
        code=stock.code,
        report_date=date.fromisoformat(profit["statDate"]),
        published_date=date.fromisoformat(profit["pubDate"]),
        fiscal_year=year,
        fiscal_quarter=quarter,
        values=values,
        cash_dividend_ttm=_query_dividend_ttm(stock.code, valuation_date),
    )


def load_financial_snapshot(
    stock: IndustryStockProfile,
    valuation_date: date,
    *,
    refresh_cache: bool = False,
) -> FinancialSnapshot:
    if not refresh_cache:
        cached = _read_cache(stock.code, valuation_date)
        if cached is not None:
            return cached
    with _BAOSTOCK_LOCK:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败: {login.error_msg}")
        try:
            snapshot = _fetch_snapshot(stock, valuation_date)
        finally:
            try:
                bs.logout()
            except Exception:
                logger.debug("Baostock logout failed", exc_info=True)
    _write_cache(snapshot, valuation_date)
    return snapshot
