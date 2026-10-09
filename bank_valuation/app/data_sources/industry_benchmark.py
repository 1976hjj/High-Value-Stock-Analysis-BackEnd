"""Live, cached cross-sectional A-share bank benchmark from Baostock."""
from __future__ import annotations
from datetime import date, timedelta
import csv
import logging
from pathlib import Path
import statistics
import time
import baostock as bs
from ..models import BankInput, IndustryBenchmarkResponse, IndustryMetric
from .bank_base import BANK_NAMES, DEFAULT_BANK_ASSUMPTIONS, _BAOSTOCK_LOCK, _latest_report, _num, _rows
from .akshare_bank_metrics import fetch_bank_special_metrics
from .access_policy import network_allowed, require_network
from . import bank_statistics

logger = logging.getLogger("bank_valuation.industry")
_BENCHMARK_TTL_SECONDS = 86400.0
_benchmark_cache: dict[str, tuple[float, dict[str, list[float]]]] = {}
_BENCHMARK_CACHE_DIR = Path("output") / "industry_benchmark"


def _quantile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    low, high = int(index), min(int(index) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def _metric(values: list[float], current: float) -> IndustryMetric:
    return IndustryMetric(
        current_value=round(current, 6), average=round(statistics.fmean(values), 6), median=round(statistics.median(values), 6),
        p25=round(_quantile(values, .25), 6), p75=round(_quantile(values, .75), 6),
        percentile=round(sum(value <= current for value in values) / len(values), 6), sample_size=len(values),
    )


def _cache_path(as_of: date) -> Path:
    return _BENCHMARK_CACHE_DIR / f"a_share_banks_{as_of.isoformat()}.csv"


def _read_csv_cache(as_of: date) -> dict[str, list[float]] | None:
    path = _cache_path(as_of)
    if not path.exists() or (network_allowed() and time.time() - path.stat().st_mtime >= _BENCHMARK_TTL_SECONDS):
        return None
    if _disclosure_stamp() > path.stat().st_mtime_ns:
        return None
    values: dict[str, list[float]] = {}
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                if row.get('statistics_version') != '2':
                    return None
                values.setdefault(row["metric"], []).append(float(row["value"]))
        required_metrics = {"pb", "pe", "roe", "profit_growth_yoy", "nim", "net_interest_spread", "npl_ratio", "provision_coverage", "loan_provision_ratio", "cet1_ratio", "capital_adequacy_ratio", "loan_to_deposit_ratio"}
        if values and (required_metrics.issubset(values) or (not network_allowed() and {'pb', 'pe', 'roe', 'profit_growth_yoy'}.issubset(values))):
            logger.info("Industry benchmark CSV cache hit: path=%s", path)
            return values
    except (OSError, KeyError, ValueError):
        logger.warning("Industry benchmark CSV cache unreadable; rebuilding: path=%s", path, exc_info=True)
    return None


def _write_csv_cache(as_of: date, peer_data: dict[str, list[float]]) -> None:
    path = _cache_path(as_of)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["as_of_date", "metric", "sample_index", "value", "statistics_version"])
        writer.writeheader()
        for metric, values in peer_data.items():
            for index, value in enumerate(values, start=1):
                writer.writerow({"as_of_date": as_of.isoformat(), "metric": metric, "sample_index": index, "value": value, "statistics_version": 2})
    temporary.replace(path)
    logger.info("Industry benchmark CSV cache written: path=%s", path)


def _disclosure_stamp() -> int:
    return max((path.stat().st_mtime_ns for path in bank_statistics.DISCLOSURE_CACHE.glob('*.json')), default=0)


def _memory_key(as_of: date) -> str:
    path = _cache_path(as_of)
    return f'{as_of}:{path.stat().st_mtime_ns if path.exists() else 0}:{_disclosure_stamp()}'


def _peer_values(as_of: date) -> dict[str, list[float]]:
    """Fetch comparable market and latest-report metrics with one Baostock session."""
    require_network()
    values = {"pb": [], "pe": [], "roe": [], "profit_growth_yoy": [], "nim": [], "net_interest_spread": [], "npl_ratio": [], "provision_coverage": [], "loan_provision_ratio": [], "cet1_ratio": [], "capital_adequacy_ratio": [], "loan_to_deposit_ratio": []}
    login = bs.login()
    if login.error_code != "0":
        raise RuntimeError(f"Baostock 登录失败: {login.error_msg}")
    try:
        for code in BANK_NAMES:
            try:
                rows = _rows(bs.query_history_k_data_plus(
                    code, "date,close,pbMRQ,peTTM", start_date=(as_of - timedelta(days=10)).isoformat(),
                    end_date=as_of.isoformat(), frequency="d", adjustflag="3",
                ), f"industry market {code}")
                valid = [row for row in rows if _num(row, "pbMRQ") > 0 and _num(row, "close") > 0]
                if not valid:
                    continue
                market = valid[-1]
                profit, growth = _latest_report(code, date.fromisoformat(market["date"]))
                month = date.fromisoformat(profit["statDate"]).month
                roe = _num(profit, "roeAvg") * 12 / month
                special = fetch_bank_special_metrics(code, date.fromisoformat(market["date"]))
                pb, price = _num(market, 'pbMRQ'), _num(market, 'close')
                peer = BankInput(stock_code=code, stock_name=BANK_NAMES[code], current_price=price,
                    bps=price / pb, eps=_num(profit, 'epsTTM'), roe=roe,
                    net_profit=_num(profit, 'netProfit'), profit_growth_yoy=_num(growth, 'YOYNI'),
                    dividend_per_share=0, payout_ratio=0, dividend_yield=0,
                    pb_current=pb, pe_current=_num(market, 'peTTM') or None, pb_history=[pb],
                    market_date=date.fromisoformat(market['date']),
                    financial_report_date=date.fromisoformat(profit['statDate']), **DEFAULT_BANK_ASSUMPTIONS)
                peer = bank_statistics.correct_bank_statistics(peer)
                # Never label an uncorrected provider PE/ROE as the new basis.
                if peer.statistics_version != 2 or not peer.eps_basis or not peer.roe_basis:
                    logger.warning('Skip peer without ordinary-share disclosures: code=%s', code)
                    continue
                if peer.market_date != as_of:
                    continue
                for key, field in [('pb', 'pb_current'), ('pe', 'pe_current'), ('roe', 'roe'), ('profit_growth_yoy', 'profit_growth_yoy')]:
                    value = getattr(peer, field)
                    if value is not None:
                        values[key].append(value)
                if special:
                    for key in ("nim", "net_interest_spread", "npl_ratio", "provision_coverage", "loan_provision_ratio", "cet1_ratio", "capital_adequacy_ratio", "loan_to_deposit_ratio"):
                        value = getattr(peer, key)
                        if value is not None:
                            values[key].append(value)
            except RuntimeError as exc:
                # One missing peer must not invalidate the entire banking sample.
                logger.warning("Skip industry peer: code=%s error=%s", code, exc)
        return {key: value for key, value in values.items() if value}
    finally:
        try:
            bs.logout()
        except Exception:
            logger.warning("Industry benchmark logout failed", exc_info=True)


def get_industry_benchmark(bank: BankInput) -> IndustryBenchmarkResponse:
    as_of = bank.market_date or date.today()
    cache_key = _memory_key(as_of)
    with _BAOSTOCK_LOCK:
        cached = _benchmark_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < _BENCHMARK_TTL_SECONDS:
            peer_data = cached[1]
            logger.info("Industry benchmark memory cache hit: as_of=%s", as_of)
        else:
            peer_data = _read_csv_cache(as_of)
            if peer_data is None:
                logger.info("Start industry benchmark: as_of=%s peers=%d", as_of, len(BANK_NAMES))
                if not network_allowed():
                    from ..valuation.mean_reversion import _load_latest_cached_bank
                    mapping = {'pb': 'pb_current', 'pe': 'pe_current', 'roe': 'roe',
                               'profit_growth_yoy': 'profit_growth_yoy', 'nim': 'nim',
                               'net_interest_spread': 'net_interest_spread', 'npl_ratio': 'npl_ratio',
                               'provision_coverage': 'provision_coverage', 'loan_provision_ratio': 'loan_provision_ratio',
                               'cet1_ratio': 'cet1_ratio', 'capital_adequacy_ratio': 'capital_adequacy_ratio',
                               'loan_to_deposit_ratio': 'loan_to_deposit_ratio'}
                    peer_data = {}
                    for code in BANK_NAMES:
                        try:
                            peer = _load_latest_cached_bank(code, as_of)
                        except RuntimeError:
                            continue
                        if peer.market_date != as_of:
                            continue
                        for key, field in mapping.items():
                            value = getattr(peer, field)
                            if value is not None:
                                peer_data.setdefault(key, []).append(value)
                    if not peer_data:
                        require_network()
                else:
                    peer_data = _peer_values(as_of)
                _write_csv_cache(as_of, peer_data)
            _benchmark_cache[_memory_key(as_of)] = (time.monotonic(), peer_data)
            logger.info("Industry benchmark complete: as_of=%s samples=%s", as_of, {k: len(v) for k, v in peer_data.items()})

    current_values = {"pb": bank.pb_current, "pe": bank.pe_current, "roe": bank.roe, "profit_growth_yoy": bank.profit_growth_yoy, "nim": bank.nim, "net_interest_spread": bank.net_interest_spread, "npl_ratio": bank.npl_ratio, "provision_coverage": bank.provision_coverage, "loan_provision_ratio": bank.loan_provision_ratio, "cet1_ratio": bank.cet1_ratio, "capital_adequacy_ratio": bank.capital_adequacy_ratio, "loan_to_deposit_ratio": bank.loan_to_deposit_ratio}
    metrics = {key: _metric(values, float(current_values[key])) for key, values in peer_data.items() if current_values.get(key) is not None}
    # The headline count is the broad market-comparison universe (PB/PE). Each
    # individual card exposes its own sample size because bank-only disclosures
    # are not uniformly available across every listed bank.
    sample_size = metrics["pb"].sample_size if "pb" in metrics else 0
    return IndustryBenchmarkResponse(
        industry_name="A股银行", as_of_date=as_of, sample_size=sample_size, metrics=metrics,
        data_note=f"同一行情日比较：PB来自 Baostock，PE按普通股基本EPS TTM计算；ROE优先使用披露的普通股加权年化值，否则为报告期加权ROE按月份年化估算。净利润同比及监管指标来自估值日前已公告财务记录；缺失指标不进入该项样本。基准版本2：{_cache_path(as_of)}。",
    )
