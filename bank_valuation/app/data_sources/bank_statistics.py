"""Comparable ordinary-share bank statistics, calculated from dated disclosures.

The headline yield uses the latest *completed* fiscal year's dividends. Paid
365-day cash is a separate measure; a new interim dividend is never a full year.
No network access is performed while correcting a cached snapshot.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
import math
from pathlib import Path

from ..models import BankInput

DISCLOSURE_CACHE = Path('output/bank_disclosures')
OVERRIDES = Path(__file__).with_name('bank_disclosure_overrides.json')


def number(value) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def read_disclosures(code: str) -> dict | None:
    path = DISCLOSURE_CACHE / f'{code.replace(".", "_")}.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def write_disclosures(code: str, **datasets) -> None:
    """Update only the supplied source datasets, using atomic replacement."""
    payload = read_disclosures(code) or {'code': code}
    payload.update(datasets)
    DISCLOSURE_CACHE.mkdir(parents=True, exist_ok=True)
    path = DISCLOSURE_CACHE / f'{code.replace(".", "_")}.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temp.replace(path)


def disclosure_overrides(code: str, as_of: date) -> list[dict]:
    payload = json.loads(OVERRIDES.read_text(encoding='utf-8'))
    return [item for item in payload if item['code'] == code and day(item['published_date']) <= as_of]


def dividend_records(rows) -> list[dict]:
    """Normalize named Eastmoney/AKShare fields and remove repeated events."""
    records = {}
    for row in rows:
        report = day(row.get('REPORT_DATE', row.get('报告期', row.get('report_date'))))
        announcement = day(row.get('PLAN_NOTICE_DATE', row.get('业绩披露日期', row.get('announcement_date'))))
        ex_date = day(row.get('EX_DIVIDEND_DATE', row.get('除权除息日', row.get('ex_dividend_date'))))
        cash = number(row.get('PRETAX_BONUS_RMB', row.get('现金分红-现金分红比例')))
        if cash is not None:
            cash /= 10  # Upstream cash is per TEN shares, pre-tax.
        else:
            cash = number(row.get('cash_per_share'))
        bonus = number(row.get('BONUS_IT_RATIO', row.get('送转股份-送转总比例', row.get('bonus_per_ten')))) or 0
        if cash is None and bonus > 0:
            cash = 0.0  # Pure stock distributions still alter the share basis.
        if report is None or announcement is None or cash is None or cash < 0:
            continue
        key = (report, ex_date, round(cash, 8))
        candidate = {'report_date': report, 'announcement_date': announcement,
                     'ex_dividend_date': ex_date, 'cash_per_share': cash,
                     'bonus_per_ten': bonus}
        if key not in records or announcement < records[key]['announcement_date']:
            records[key] = candidate
    return list(records.values())


def split_factor(records: list[dict], since: date, as_of: date, *, inclusive=False) -> float:
    factor = 1.0
    seen = set()
    for row in records:
        ex = row['ex_dividend_date']
        if ex and row['announcement_date'] <= as_of and (ex >= since if inclusive else ex > since) and ex <= as_of and row['bonus_per_ten'] > 0:
            marker = (ex, row['bonus_per_ten'])
            if marker not in seen:
                factor *= 1 + row['bonus_per_ten'] / 10
                seen.add(marker)
    return factor


def dividend_summary(records: list[dict], as_of: date) -> dict:
    visible = [row for row in records if row['announcement_date'] <= as_of and row['report_date'] <= as_of]
    complete_years = {row['report_date'].year for row in visible if row['report_date'].month == 12}
    fiscal_year = max(complete_years, default=None)
    def adjusted(row):
        return row['cash_per_share'] / split_factor(visible, row['ex_dividend_date'] or row['report_date'], as_of, inclusive=True)
    annual = sum(adjusted(row) for row in visible if row['report_date'].year == fiscal_year) if fiscal_year else None
    paid = sum(adjusted(row) for row in visible
               if row['ex_dividend_date'] and as_of - timedelta(days=365) < row['ex_dividend_date'] <= as_of)
    yearly = {year: sum(adjusted(row) for row in visible if row['report_date'].year == year)
              for year in complete_years}
    stable = None
    if fiscal_year and all(year in yearly for year in range(fiscal_year - 2, fiscal_year + 1)):
        stable = all(yearly[y] > 0 and yearly[y] >= yearly[y-1] * .9
                     for y in (fiscal_year - 1, fiscal_year))
    return {'year': fiscal_year, 'annual': annual, 'paid_ttm': paid, 'stable': stable}


def correct_bank_statistics(bank: BankInput) -> BankInput:
    payload = read_disclosures(bank.stock_code)
    if not payload or not bank.market_date:
        return bank
    as_of = bank.market_date
    overrides = disclosure_overrides(bank.stock_code, as_of)
    records = dividend_records(payload.get('dividends', []))
    for item in overrides:
        if 'annual_dividend_per_share' in item:
            records.append({'report_date': day(item['report_date']),
                            'announcement_date': day(item['published_date']),
                            'ex_dividend_date': None, 'cash_per_share': item['annual_dividend_per_share'],
                            'bonus_per_ten': 0})
    summary = dividend_summary(records, as_of)
    updates = {'statistics_version': 2, 'data_quality_notes': [],
               'dividend_fiscal_year': summary['year'], 'dividend_basis': 'latest_complete_fiscal_year',
               'dividend_cash_ttm': summary['paid_ttm'],
               'dividend_yield_ttm': summary['paid_ttm'] / bank.current_price,
               'dividend_stable': summary['stable']}
    if summary['annual'] is not None:
        updates.update(dividend_per_share=summary['annual'], dividend_yield=summary['annual'] / bank.current_price)
    else:
        updates['data_quality_notes'].append('缺少完整年度分红记录，股息口径待核实。')
    visible = [row for row in payload.get('finance', [])
               if day(row.get('NOTICE_DATE')) and day(row['NOTICE_DATE']) <= as_of
               and day(row.get('REPORT_DATE')) and day(row['REPORT_DATE']) <= as_of]
    visible.sort(key=lambda row: (row['REPORT_DATE'], row['NOTICE_DATE']), reverse=True)
    if not visible:
        updates['data_quality_notes'].append('普通股股东财务指标缺失，保留原始财务口径。')
        return bank.model_copy(update=updates)
    report = visible[0]
    report_date = day(report['REPORT_DATE'])
    by_date = {}
    for row in visible:
        by_date.setdefault(day(row['REPORT_DATE']), row)
    weighted_roe = number(report.get('ROEJQ'))
    updates.update(financial_report_date=report_date, financial_published_date=day(report['NOTICE_DATE']),
                   financial_metrics_source='Eastmoney / published ordinary-share financial indicators')
    if weighted_roe is not None:
        updates.update(roe_reported=weighted_roe / 100,
                       roe=weighted_roe / 100 * 12 / report_date.month,
                       roe_basis='weighted_ordinary_share_annualized_estimate')
    # EPSJB is cumulative ordinary-share basic EPS, NOT TTM. Build TTM explicitly.
    current_eps = number(report.get('EPSJB'))
    annual_report = by_date.get(date(report_date.year - 1, 12, 31))
    same_period = by_date.get(date(report_date.year - 1, report_date.month, report_date.day))
    eps_ttm = current_eps if report_date.month == 12 else None
    if report_date.month != 12 and current_eps is not None and annual_report and same_period:
        annual_eps, previous_eps = number(annual_report.get('EPSJB')), number(same_period.get('EPSJB'))
        if annual_eps is not None and previous_eps is not None:
            annual_eps /= split_factor(records, day(annual_report['REPORT_DATE']), report_date)
            previous_eps /= split_factor(records, day(same_period['REPORT_DATE']), report_date)
            eps_ttm = annual_eps + current_eps - previous_eps
    if eps_ttm is not None:
        eps_ttm /= split_factor(records, report_date, as_of)
        updates.update(eps=eps_ttm, eps_basis='ordinary_share_basic_eps_ttm',
                       pe_current=bank.current_price / eps_ttm if eps_ttm > 0 else None)
    else:
        updates['data_quality_notes'].append('缺少同年/上年同期 EPS，滚动普通股 EPS 尚未校正。')
    for field, source in [('net_profit', 'PARENTNETPROFIT'), ('profit_growth_yoy', 'PARENTNETPROFITTZ')]:
        value = number(report.get(source))
        if value is not None:
            updates[field] = value / 100 if field == 'profit_growth_yoy' else value
    # Payout matches dividend fiscal year to the same year's ordinary-share EPS.
    fiscal_year = summary['year']
    fiscal_report = by_date.get(date(fiscal_year, 12, 31)) if fiscal_year else None
    if fiscal_report and summary['annual'] is not None:
        annual_eps = number(fiscal_report.get('EPSJB'))
        if annual_eps is not None and annual_eps > 0:
            annual_eps /= split_factor(records, day(fiscal_report['REPORT_DATE']), as_of)
            updates.update(dividend_annual_eps=annual_eps, payout_ratio=summary['annual'] / annual_eps,
                           payout_basis='annual_dividend_same_year_ordinary_basic_eps_estimate')
    # Compare each regulatory reading with the previous year-end, rather than
    # silently treating unobserved change as zero/stable.
    previous = by_date.get(date(report_date.year - 1, 12, 31))
    mapping = {'nim': 'NET_INTEREST_MARGIN', 'npl_ratio': 'NONPERLOAN',
               'provision_coverage': 'BLDKBBL', 'cet1_ratio': 'HXYJBCZL',
               'capital_adequacy_ratio': 'NEWCAPITALADER', 'net_interest_spread': 'NET_INTEREST_SPREAD',
               'loan_provision_ratio': 'LOAN_PROVISION_RATIO'}
    for field, source in mapping.items():
        value = number(report.get(source))
        updates[field] = value / 100 if value is not None and value > 0 else None
        change = field + '_change'
        if change in {'nim_change', 'npl_ratio_change', 'provision_coverage_change'}:
            prior = number(previous.get(source)) if previous else None
            updates[change] = (value-prior)/100 if value is not None and prior is not None else None
    loans, deposits = number(report.get('GROSSLOANS')), number(report.get('TOTALDEPOSITS'))
    updates['loan_to_deposit_ratio'] = loans / deposits if loans is not None and deposits and deposits > 0 else None
    updates.update(bank_special_metrics_source='AKShare / Eastmoney financial indicators',
                   bank_special_metrics_report_date=report_date, provision_coverage_report_date=report_date,
                   provision_coverage_source='AKShare / Eastmoney financial indicators',
                   trend_comparison_date=day(previous['REPORT_DATE']) if previous else None)
    # Keep official annualized ROE when verified from a dated bank report.
    for item in overrides:
        if day(item['report_date']) == report_date and 'roe_annualized' in item:
            updates.update(roe=item['roe_annualized'], roe_basis='reported_weighted_ordinary_share_annualized')
        if item.get('fiscal_year') == fiscal_year and 'ordinary_profit' in item:
            cash = summary['annual'] * item['ordinary_shares']
            updates.update(payout_ratio=cash/item['ordinary_profit'], payout_basis='annual_dividend_same_year_ordinary_profit')
    return bank.model_copy(update=updates)
