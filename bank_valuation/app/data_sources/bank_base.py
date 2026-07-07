"""Build a valuation-ready bank snapshot from Baostock daily and report data."""
from __future__ import annotations
from datetime import date, timedelta
import csv
import logging
from pathlib import Path
import threading
import time
import baostock as bs
from ..models import BankInput
from .akshare_bank_metrics import fetch_bank_special_metrics


# Baostock does not reliably publish these bank-specific indicators.  They are
# explicit conservative defaults, rather than invented "real-time" values.
DEFAULT_BANK_ASSUMPTIONS = {
    "risk_free_rate": 0.02, "equity_risk_premium": 0.06, "beta": 0.8,
    "long_term_growth": 0.03,
}
DEFAULT_PAYOUT_RATIO = 0.4
DIVIDEND_LOOKBACK_DAYS = 365
logger = logging.getLogger("bank_valuation.data")
_BAOSTOCK_LOCK = threading.RLock()
_CACHE_TTL_SECONDS = 120.0
_data_cache: dict[tuple[str, str], tuple[float, BankInput]] = {}
_BANK_CACHE_DIR = Path("output") / "bank_cache"

# Baostock's company-name encoding can vary by environment. This service is
# specifically for A-share banks, so known names are kept deterministic.
BANK_NAMES = {
    "sh.601398": "工商银行", "sh.601939": "建设银行", "sh.601288": "农业银行",
    "sh.601988": "中国银行", "sh.600036": "招商银行", "sh.601328": "交通银行",
    "sh.601166": "兴业银行", "sh.600000": "浦发银行", "sh.600016": "民生银行",
    "sh.600015": "华夏银行", "sh.601998": "中信银行", "sh.601818": "光大银行",
    "sh.601169": "北京银行", "sh.601229": "上海银行", "sh.601009": "南京银行",
    "sh.601838": "成都银行", "sh.601577": "长沙银行", "sh.601128": "常熟银行",
    "sh.603323": "苏农银行", "sh.601963": "重庆银行", "sz.000001": "平安银行",
    "sz.002142": "宁波银行", "sz.002807": "江阴银行", "sz.002839": "张家港行",
    "sz.002948": "青岛银行", "sz.002958": "青农商行", "sz.002966": "苏州银行",
    "sh.601658": "邮储银行", "sh.601825": "上海农商银行", "sh.601077": "渝农商行",
    "sh.600919": "江苏银行", "sh.600926": "杭州银行", "sh.600928": "西安银行",
    "sh.601916": "浙商银行", "sh.601665": "齐鲁银行", "sh.601187": "厦门银行",
    "sh.601860": "紫金银行", "sh.601528": "瑞丰银行", "sh.601997": "贵阳银行",
    "sh.600908": "无锡银行", "sz.001227": "兰州银行", "sz.002936": "郑州银行",
}


def normalize_code(code: str) -> str:
    code = code.strip().lower()
    if "." in code:
        return code
    if not code.isdigit() or len(code) != 6:
        raise ValueError("stock_code 必须是 6 位代码或 Baostock 格式，例如 601398、sh.601398")
    return ("sh." if code.startswith(("5", "6", "9")) else "sz.") + code


def _rows(query, source: str) -> list[dict[str, str]]:
    if query.error_code != "0":
        logger.error("Baostock %s query failed: code=%s message=%s", source, query.error_code, query.error_msg)
        raise RuntimeError(f"Baostock 查询失败: {query.error_msg}")
    output = []
    while query.next():
        output.append(dict(zip(query.fields, query.get_row_data())))
    logger.info("Baostock %s returned %d rows", source, len(output))
    return output


def _num(row: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(row.get(key) or default)
    except ValueError:
        return default


def _is_blank(value: str | None) -> bool:
    return value in {None, "", "None", "none", "null", "NULL"}


def _optional_float(row: dict[str, str], key: str) -> float | None:
    raw = row.get(key)
    if _is_blank(raw):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    # Bank-specific regulatory/operating ratios such as NIM, NPL, coverage,
    # CET1 and CAR should not silently become 0 when upstream data is missing.
    # A real zero for these fields is economically implausible for listed banks.
    return None if value == 0 else value


def _cache_stem(code: str) -> str:
    return code.replace(".", "_")


def _snapshot_path(code: str) -> Path:
    return _BANK_CACHE_DIR / f"{_cache_stem(code)}_snapshots.csv"


def _market_path(code: str) -> Path:
    return _BANK_CACHE_DIR / f"{_cache_stem(code)}_market_history.csv"


def _write_disk_cache(requested_date: date, bank: BankInput) -> None:
    """Persist a reproducible input snapshot and the daily market series used by it."""
    _BANK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = _snapshot_path(bank.stock_code)
    snapshot_fields = [
        "requested_date", "stock_code", "stock_name", "market_date", "financial_report_date", "current_price", "daily_change_pct", "bps", "eps", "roe", "net_profit", "profit_growth_yoy", "dividend_per_share", "payout_ratio", "dividend_yield", "pb_current", "pe_current", "nim", "npl_ratio", "provision_coverage", "provision_coverage_report_date", "provision_coverage_source", "cet1_ratio", "capital_adequacy_ratio", "net_interest_spread", "loan_provision_ratio", "loan_to_deposit_ratio", "bank_special_metrics_report_date", "bank_special_metrics_source", "risk_free_rate", "equity_risk_premium", "beta", "long_term_growth", "roe_trend", "nim_change", "npl_ratio_change", "provision_coverage_change", "dividend_stable",
    ]
    existing: list[dict[str, str]] = []
    if snapshot.exists():
        with snapshot.open("r", encoding="utf-8", newline="") as file:
            existing = list(csv.DictReader(file))
    row = {field: str(getattr(bank, field)) for field in snapshot_fields if field not in {"requested_date"}}
    row["requested_date"] = requested_date.isoformat()
    # A retry or forced refresh replaces the same requested-date row rather than duplicating it.
    existing = [item for item in existing if item.get("requested_date") != row["requested_date"]]
    existing.append(row)
    existing.sort(key=lambda item: item["requested_date"])
    with snapshot.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=snapshot_fields)
        writer.writeheader(); writer.writerows(existing)

    market = _market_path(bank.stock_code)
    market_rows: dict[str, dict[str, str]] = {}
    if market.exists():
        with market.open("r", encoding="utf-8", newline="") as file:
            market_rows = {item["date"]: item for item in csv.DictReader(file) if item.get("date")}
    for index, point_date in enumerate(bank.pb_history_dates):
        if index < len(bank.price_history) and index < len(bank.pb_history):
            market_rows[str(point_date)] = {"date": str(point_date), "close": str(bank.price_history[index]), "pb": str(bank.pb_history[index])}
    with market.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["date", "close", "pb"])
        writer.writeheader(); writer.writerows(sorted(market_rows.values(), key=lambda item: item["date"]))
    logger.info("Bank CSV cache written: snapshot=%s market=%s", snapshot, market)


def _read_disk_cache(code: str, requested_date: date, allow_stale_current: bool = False) -> BankInput | None:
    snapshot, market = _snapshot_path(code), _market_path(code)
    if not snapshot.exists() or not market.exists():
        return None

    try:
        with snapshot.open("r", encoding="utf-8", newline="") as file:
            row = next((item for item in csv.DictReader(file) if item.get("requested_date") == requested_date.isoformat()), None)
        if row is None:
            return None
        market_date = date.fromisoformat(row["market_date"])
        if not allow_stale_current and requested_date >= date.today() and market_date < requested_date:
            logger.info(
                "Bank CSV cache is stale for current/future request; will refresh: code=%s requested_date=%s cached_market_date=%s",
                code, requested_date, market_date,
            )
            return None
        history_start = market_date - timedelta(days=3660)
        with market.open("r", encoding="utf-8", newline="") as file:
            history = [item for item in csv.DictReader(file) if history_start <= date.fromisoformat(item["date"]) <= market_date]
        if not history:
            return None
        def value(key: str, default: float = 0.0) -> float:
            raw = row.get(key)
            return default if raw in {None, "", "None", "null"} else float(raw)
        if not _is_blank(row.get("daily_change_pct")):
            daily_change_pct = value("daily_change_pct")
        elif len(history) >= 2 and float(history[-2]["close"]) > 0:
            daily_change_pct = float(history[-1]["close"]) / float(history[-2]["close"]) - 1
        else:
            daily_change_pct = None
        restored = BankInput(
            stock_code=row["stock_code"], stock_name=row["stock_name"], current_price=value("current_price"), daily_change_pct=daily_change_pct, bps=value("bps"), eps=value("eps"), roe=value("roe"), net_profit=value("net_profit"), profit_growth_yoy=value("profit_growth_yoy"), dividend_per_share=value("dividend_per_share"), payout_ratio=value("payout_ratio"), dividend_yield=value("dividend_yield"), pb_current=value("pb_current"), pe_current=value("pe_current") if not _is_blank(row.get("pe_current")) else None, pb_history=[float(item["pb"]) for item in history], nim=_optional_float(row, "nim"), npl_ratio=_optional_float(row, "npl_ratio"), provision_coverage=_optional_float(row, "provision_coverage"), provision_coverage_report_date=date.fromisoformat(row["provision_coverage_report_date"]) if not _is_blank(row.get("provision_coverage_report_date")) else None, provision_coverage_source=row.get("provision_coverage_source") if not _is_blank(row.get("provision_coverage_source")) else None, cet1_ratio=_optional_float(row, "cet1_ratio"), capital_adequacy_ratio=_optional_float(row, "capital_adequacy_ratio"), net_interest_spread=_optional_float(row, "net_interest_spread"), loan_provision_ratio=_optional_float(row, "loan_provision_ratio"), loan_to_deposit_ratio=_optional_float(row, "loan_to_deposit_ratio"), risk_free_rate=value("risk_free_rate"), equity_risk_premium=value("equity_risk_premium"), beta=value("beta"), long_term_growth=value("long_term_growth"), market_date=market_date, financial_report_date=date.fromisoformat(row["financial_report_date"]), pb_history_dates=[date.fromisoformat(item["date"]) for item in history], price_history=[float(item["close"]) for item in history], roe_trend=value("roe_trend"), nim_change=value("nim_change"), npl_ratio_change=value("npl_ratio_change"), provision_coverage_change=value("provision_coverage_change"), dividend_stable=row.get("dividend_stable", "True").lower() == "true",
        )
        # Legacy CSV files contained synthetic bank-specific defaults. Only use
        # these fields when a real source marker is present.
        source = row.get("bank_special_metrics_source") if not _is_blank(row.get("bank_special_metrics_source")) else None
        if not source:
            return restored.model_copy(update={"nim": None, "npl_ratio": None, "provision_coverage": None, "cet1_ratio": None, "capital_adequacy_ratio": None, "net_interest_spread": None, "loan_provision_ratio": None, "loan_to_deposit_ratio": None})
        report_date_text = row.get("bank_special_metrics_report_date")
        return restored.model_copy(update={
            "bank_special_metrics_source": source,
            "bank_special_metrics_report_date": date.fromisoformat(report_date_text) if not _is_blank(report_date_text) else None,
        })
    except (OSError, ValueError, KeyError):
        logger.warning("Bank CSV cache unreadable; will refresh: code=%s requested_date=%s", code, requested_date, exc_info=True)
        return None


def _enrich_bank_special_metrics(bank: BankInput) -> BankInput:
    """Fill bank-only indicators from AKShare without overwriting supplied data."""
    if bank.bank_special_metrics_source and all(value is not None for value in (bank.nim, bank.npl_ratio, bank.provision_coverage, bank.cet1_ratio, bank.capital_adequacy_ratio, bank.net_interest_spread, bank.loan_provision_ratio, bank.loan_to_deposit_ratio)):
        return bank
    metrics = fetch_bank_special_metrics(bank.stock_code, bank.market_date or date.today())
    if metrics is None:
        return bank
    return bank.model_copy(update={
        "nim": bank.nim if bank.nim is not None else metrics.nim,
        "npl_ratio": bank.npl_ratio if bank.npl_ratio is not None else metrics.npl_ratio,
        "provision_coverage": bank.provision_coverage if bank.provision_coverage is not None else metrics.provision_coverage,
        "cet1_ratio": bank.cet1_ratio if bank.cet1_ratio is not None else metrics.cet1_ratio,
        "capital_adequacy_ratio": bank.capital_adequacy_ratio if bank.capital_adequacy_ratio is not None else metrics.capital_adequacy_ratio,
        "net_interest_spread": bank.net_interest_spread if bank.net_interest_spread is not None else metrics.net_interest_spread,
        "loan_provision_ratio": bank.loan_provision_ratio if bank.loan_provision_ratio is not None else metrics.loan_provision_ratio,
        "loan_to_deposit_ratio": bank.loan_to_deposit_ratio if bank.loan_to_deposit_ratio is not None else metrics.loan_to_deposit_ratio,
        "provision_coverage_report_date": bank.provision_coverage_report_date or metrics.report_date,
        "provision_coverage_source": bank.provision_coverage_source or metrics.source,
        "bank_special_metrics_report_date": metrics.report_date,
        "bank_special_metrics_source": metrics.source,
    })


def _latest_report(code: str, as_of: date) -> tuple[dict[str, str], dict[str, str]]:
    for year in range(as_of.year, as_of.year - 3, -1):
        for quarter in (4, 3, 2, 1):
            profit = _rows(bs.query_profit_data(code=code, year=year, quarter=quarter), f"profit {year}Q{quarter}")
            growth = _rows(bs.query_growth_data(code=code, year=year, quarter=quarter), f"growth {year}Q{quarter}")
            if not profit or not growth:
                continue
            pub = profit[0].get("pubDate", "")
            try:
                if date.fromisoformat(pub) <= as_of:
                    report_date = date.fromisoformat(profit[0]["statDate"])
                    logger.info("Selected financial report: code=%s report_date=%s published=%s", code, report_date, pub)
                    return profit[0], growth[0]
            except ValueError:
                continue
    raise RuntimeError("指定日期之前没有可用的 Baostock 财务报告")


def _dividend_event_date(row: dict[str, str]) -> date | None:
    for key in ("dividOperateDate", "dividPayDate"):
        raw = row.get(key)
        if _is_blank(raw):
            continue
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _dividend_announce_date(row: dict[str, str]) -> date | None:
    for key in ("dividPlanAnnounceDate", "dividPreNoticeDate", "dividAgmPumDate", "dividPlanDate"):
        raw = row.get(key)
        if _is_blank(raw):
            continue
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _is_annual_dividend(row: dict[str, str], announce_date: date | None) -> bool:
    if not _is_blank(row.get("dividAgmPumDate")):
        return True
    return announce_date is not None and announce_date.month <= 6


def _dividend_report_year(row: dict[str, str], announce_date: date | None, is_annual: bool) -> int | None:
    raw_year = row.get("statYear") or row.get("year") or row.get("dividYear")
    if not _is_blank(raw_year):
        try:
            return int(float(raw_year))
        except ValueError:
            pass
    if announce_date is None:
        return None
    return announce_date.year - 1 if is_annual else announce_date.year


def _dividend_event_key(
    report_year: int | None,
    is_annual: bool,
    event_date: date,
    cash: float,
) -> tuple[int | None, bool, str, float]:
    return report_year, is_annual, event_date.isoformat(), round(cash, 8)


def _trailing_dividend(code: str, as_of: date) -> float:
    events: list[tuple[int | None, bool, date, float, bool]] = []
    seen: set[tuple[int | None, bool, str, float]] = set()
    window_start = as_of - timedelta(days=DIVIDEND_LOOKBACK_DAYS)
    duplicate_count = 0
    for year in (as_of.year, as_of.year - 1, as_of.year - 2):
        for row in _rows(bs.query_dividend_data(code=code, year=year, yearType="report"), f"dividend {year}"):
            event_date = _dividend_event_date(row)
            if event_date is None:
                continue
            row_cash = _num(row, "dividCashPsBeforeTax")
            if row_cash <= 0:
                continue
            announce_date = _dividend_announce_date(row)
            is_annual = _is_annual_dividend(row, announce_date)
            report_year = _dividend_report_year(row, announce_date, is_annual)
            event_key = _dividend_event_key(report_year, is_annual, event_date, row_cash)
            if event_key in seen:
                duplicate_count += 1
                continue
            seen.add(event_key)
            is_paid_in_window = window_start <= event_date <= as_of
            is_announced = announce_date is not None and announce_date <= as_of
            if is_paid_in_window or (is_annual and is_announced):
                events.append((report_year, is_annual, event_date, row_cash, is_paid_in_window))

    latest_announced_annual_year = max(
        (
            report_year
            for report_year, is_annual, _event_date, _cash, _is_paid in events
            if is_annual and report_year is not None
        ),
        default=None,
    )
    if latest_announced_annual_year is None:
        selected_events = [event for event in events if event[4]]
    else:
        selected_events = [
            event
            for event in events
            if (event[1] and event[0] == latest_announced_annual_year) or (not event[1] and event[4])
        ]
    cash = sum(event[3] for event in selected_events)
    logger.info(
        "Trailing cash dividend: code=%s as_of=%s dividend_per_share=%.6f selected_events=%d unique_events=%d skipped_duplicates=%d",
        code,
        as_of,
        cash,
        len(selected_events),
        len(seen),
        duplicate_count,
    )
    return cash


def _load_bank_input_once(stock_code: str, valuation_date: date | None = None) -> BankInput:
    """Get daily market data plus the latest report available on the given date."""
    code = normalize_code(stock_code)
    requested_date = valuation_date or date.today()
    logger.info("Start data load: request_code=%s normalized_code=%s requested_date=%s", stock_code, code, requested_date)
    login = bs.login()
    if login.error_code != "0":
        logger.error("Baostock login failed: code=%s message=%s", login.error_code, login.error_msg)
        raise RuntimeError(f"Baostock 登录失败: {login.error_msg}")
    logger.info("Baostock login succeeded: code=%s", code)
    try:
        # 10-year PB history; the last available trading day on or before the date is used.
        start = requested_date - timedelta(days=3660)
        market = _rows(bs.query_history_k_data_plus(
            code, "date,code,close,pbMRQ", start_date=start.isoformat(), end_date=requested_date.isoformat(),
            frequency="d", adjustflag="3",
        ), "daily price/PB")
        valid_market = [row for row in market if _num(row, "close") > 0 and _num(row, "pbMRQ") > 0]
        if not valid_market:
            raise RuntimeError("指定日期之前没有可用交易日价格/PB数据")
        latest = valid_market[-1]
        actual_date = date.fromisoformat(latest["date"])
        logger.info("Market data selected: code=%s actual_date=%s close=%s pbMRQ=%s history_rows=%d", code, actual_date, latest["close"], latest["pbMRQ"], len(valid_market))
        profit, growth = _latest_report(code, actual_date)
        close, pb = _num(latest, "close"), _num(latest, "pbMRQ")
        previous_close = _num(valid_market[-2], "close") if len(valid_market) >= 2 else 0.0
        daily_change_pct = close / previous_close - 1 if previous_close > 0 else None
        eps, roe = _num(profit, "epsTTM"), _num(profit, "roeAvg")
        # roeAvg is year-to-date report-period ROE. Annualize Q1/Q2/Q3 so it
        # is comparable with the annual ROE used by the valuation models.
        report_month = date.fromisoformat(profit["statDate"]).month
        roe *= 12 / report_month
        dividend = _trailing_dividend(code, actual_date)
        payout = min(1.0, dividend / eps) if eps > 0 and dividend > 0 else DEFAULT_PAYOUT_RATIO
        name_rows = _rows(bs.query_stock_basic(code=code), "stock basic")
        name = BANK_NAMES.get(code) or (name_rows[0].get("code_name") if name_rows else code)
        result = BankInput(
            stock_code=code, stock_name=name or code, current_price=close, daily_change_pct=daily_change_pct, bps=close / pb, eps=eps, roe=roe,
            net_profit=_num(profit, "netProfit"), profit_growth_yoy=_num(growth, "YOYNI"),
            dividend_per_share=dividend, payout_ratio=payout, dividend_yield=dividend / close if close else 0,
            pb_current=pb, pe_current=(close / eps if eps > 0 else None),
            pb_history=[_num(row, "pbMRQ") for row in valid_market], market_date=actual_date,
            financial_report_date=date.fromisoformat(profit["statDate"]),
            pb_history_dates=[date.fromisoformat(row["date"]) for row in valid_market],
            price_history=[_num(row, "close") for row in valid_market], **DEFAULT_BANK_ASSUMPTIONS,
        )
        logger.info("Data load complete: code=%s price=%.4f bps=%.4f roe=%.4f eps=%.4f", result.stock_code, result.current_price, result.bps, result.roe, result.eps)
        return result
    except Exception:
        logger.exception("Data load failed: code=%s requested_date=%s", code, requested_date)
        raise
    finally:
        try:
            bs.logout()
            logger.info("Baostock logout completed: code=%s", code)
        except Exception:
            # Connection errors can invalidate the socket before logout; retain
            # the original fetch exception instead of obscuring it here.
            logger.warning("Baostock logout skipped after an invalid connection: code=%s", code, exc_info=True)


def load_bank_input(stock_code: str, valuation_date: date | None = None, refresh_cache: bool = False) -> BankInput:
    """Load data safely despite Baostock's process-global, non-thread-safe client.

    Baostock uses shared socket state. FastAPI may serve valuation and Monte Carlo
    requests concurrently, so every complete login/query/logout sequence is
    serialized. A short cache lets the second request reuse the same snapshot.
    """
    code = normalize_code(stock_code)
    requested_date = valuation_date or date.today()
    cache_key = (code, requested_date.isoformat())
    now = time.monotonic()
    with _BAOSTOCK_LOCK:
        cached = _data_cache.get(cache_key)
        if not refresh_cache and cached and now - cached[0] < _CACHE_TTL_SECONDS:
            logger.info("Data cache hit: code=%s requested_date=%s age=%.1fs", code, requested_date, now - cached[0])
            return cached[1].model_copy(deep=True)

        if not refresh_cache:
            disk_cached = _read_disk_cache(code, requested_date)
            if disk_cached is not None:
                enriched = _enrich_bank_special_metrics(disk_cached)
                if enriched is not disk_cached:
                    _write_disk_cache(requested_date, enriched)
                    disk_cached = enriched
                _data_cache[cache_key] = (time.monotonic(), disk_cached)
                logger.info("Bank CSV cache hit: code=%s requested_date=%s", code, requested_date)
                return disk_cached.model_copy(deep=True)

        last_error: RuntimeError | None = None
        for attempt in range(1, 4):
            try:
                logger.info("Data load attempt %d/3: code=%s requested_date=%s", attempt, code, requested_date)
                result = _enrich_bank_special_metrics(_load_bank_input_once(code, requested_date))
                _data_cache[cache_key] = (time.monotonic(), result)
                _write_disk_cache(requested_date, result)
                # Keep the small cache bounded for a long-running API process.
                if len(_data_cache) > 100:
                    oldest_key = min(_data_cache, key=lambda item: _data_cache[item][0])
                    _data_cache.pop(oldest_key, None)
                return result.model_copy(deep=True)
            except RuntimeError as exc:
                last_error = exc
                is_network_error = "网络" in str(exc) or "接收" in str(exc)
                if not is_network_error or attempt == 3:
                    break
                pause = attempt * 1.0
                logger.warning("Baostock network error; retrying in %.1fs: code=%s attempt=%d error=%s", pause, code, attempt, exc)
                time.sleep(pause)

        assert last_error is not None
        raise RuntimeError(f"Baostock 数据源连接失败，已重试 3 次：{last_error}") from last_error
