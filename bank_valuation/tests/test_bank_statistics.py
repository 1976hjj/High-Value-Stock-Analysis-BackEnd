from datetime import date

import pytest

from bank_valuation.app.data_sources import bank_statistics as stats, bank_base
from bank_valuation.app.valuation.residual_income import intrinsic_value
from bank_valuation.app.valuation.mean_reversion import _quality_score
from bank_valuation.app.valuation.strategy_backtest import DividendEvent, _latest_implemented_fiscal_year_dividend


def event(report, announced, ex, cash, bonus=0):
    return {'report_date': report, 'announcement_date': announced,
            'ex_dividend_date': ex, 'cash_per_share': cash, 'BONUS_IT_RATIO': bonus}


def pingan_events():
    return [event('2025-06-30', '2025-08-23', '2025-10-15', .236),
            event('2025-12-31', '2026-03-21', '2026-06-12', .36),
            event('2026-06-30', '2026-08-15', '2026-09-24', .249)]


def test_new_interim_does_not_replace_full_annual_dividend():
    result = stats.dividend_summary(stats.dividend_records(pingan_events()), date(2026, 9, 30))
    assert result['year'] == 2025
    assert result['annual'] == pytest.approx(.596)
    assert result['paid_ttm'] == pytest.approx(.845)


def test_future_plans_and_unpaid_events_are_not_paid_ttm():
    rows = pingan_events() + [event('2026-12-31', '2027-03-01', '2027-06-01', .8)]
    result = stats.dividend_summary(stats.dividend_records(rows), date(2026, 8, 20))
    assert result['annual'] == pytest.approx(.596)
    assert result['paid_ttm'] == pytest.approx(.596)


def test_zero_annual_dividend_does_not_fall_back_to_old_payment():
    rows = [event('2024-12-31', '2025-03-01', '2025-07-10', .02),
            event('2025-12-31', '2026-03-31', None, 0)]
    result = stats.dividend_summary(stats.dividend_records(rows), date(2026, 9, 30))
    assert result['year'] == 2025
    assert result['annual'] == 0
    assert result['paid_ttm'] == 0


def test_named_cash_fields_and_duplicates_are_handled_once():
    row = {'PRETAX_BONUS_RMB': 3.6, 'EX_DIVIDEND_DATE': '2026-06-12',
           'PLAN_NOTICE_DATE': '2026-03-21', 'REPORT_DATE': '2025-12-31'}
    records = stats.dividend_records([row, dict(row)])
    assert len(records) == 1
    assert records[0]['cash_per_share'] == pytest.approx(.36)


def test_stock_bonus_adjusts_annual_and_paid_dividend_to_current_shares():
    rows = [event('2025-06-30', '2025-08-01', '2025-10-01', .13),
            event('2025-12-31', '2026-03-01', '2026-06-01', .14, bonus=1)]
    result = stats.dividend_summary(stats.dividend_records(rows), date(2026, 9, 30))
    assert result['annual'] == pytest.approx(.27 / 1.1)
    assert result['paid_ttm'] == pytest.approx(.27 / 1.1)


def test_ordinary_eps_payout_and_reported_roe_match_dated_disclosures(bank):
    finance = [
        {'REPORT_DATE': '2026-06-30', 'NOTICE_DATE': '2026-08-15', 'ROEJQ': 5.22, 'EPSJB': 1.24},
        {'REPORT_DATE': '2025-12-31', 'NOTICE_DATE': '2026-03-21', 'ROEJQ': 9.15, 'EPSJB': 2.07},
        {'REPORT_DATE': '2025-06-30', 'NOTICE_DATE': '2025-08-23', 'ROEJQ': 5.25, 'EPSJB': 1.18},
        {'REPORT_DATE': '2026-12-31', 'NOTICE_DATE': '2027-03-21', 'ROEJQ': 99, 'EPSJB': 99},
    ]
    stats.write_disclosures('sz.000001', finance=finance, dividends=pingan_events())
    result = stats.correct_bank_statistics(bank.model_copy(update={'stock_code': 'sz.000001',
        'market_date': date(2026, 9, 30), 'current_price': 11.57}))
    assert result.eps == pytest.approx(2.13)
    assert result.pe_current == pytest.approx(11.57 / 2.13)
    assert result.dividend_yield == pytest.approx(.596 / 11.57)
    assert result.dividend_yield_ttm == pytest.approx(.845 / 11.57)
    assert result.payout_ratio == pytest.approx(.596 * 19405918198 / 40114000000)
    assert result.roe == pytest.approx(.1056)
    assert result.roe_reported == pytest.approx(.0522)
    assert result.nim_change is None


def test_missing_trend_is_not_given_stability_bonus(bank):
    unknown = bank.model_copy(update={'nim_change': None, 'npl_ratio_change': None,
                                     'provision_coverage_change': None, 'dividend_stable': None})
    observed = unknown.model_copy(update={'nim_change': 0, 'npl_ratio_change': 0, 'provision_coverage_change': 0})
    assert _quality_score(observed) - _quality_score(unknown) == 8


def test_disk_snapshots_are_corrected_without_resynchronizing(monkeypatch, tmp_path, bank):
    monkeypatch.setattr(bank_base, '_BANK_CACHE_DIR', tmp_path / 'banks')
    cached = bank.model_copy(update={'stock_code': 'sz.000001', 'market_date': date(2026, 9, 30),
        'financial_report_date': date(2026, 6, 30), 'current_price': 11.57, 'dividend_per_share': .249,
        'dividend_yield': .249/11.57, 'pb_history_dates': [date(2026, 9, 30)],
        'pb_history': [.48], 'price_history': [11.57]})
    bank_base._write_disk_cache(date(2026, 10, 4), cached)
    stats.write_disclosures('sz.000001', dividends=pingan_events())
    restored = bank_base._read_disk_cache('sz.000001', date(2026, 10, 4), allow_stale_current=True)
    assert restored.dividend_per_share == pytest.approx(.596)


def test_bank_backtest_does_not_replace_year_with_interim_only():
    events = [DividendEvent(date(2025, 6, 30), date(2025, 8, 23), date(2025, 10, 15), .236),
              DividendEvent(date(2025, 12, 31), date(2026, 3, 21), date(2026, 6, 12), .36),
              DividendEvent(date(2026, 6, 30), date(2026, 8, 15), date(2026, 9, 24), .249)]
    assert _latest_implemented_fiscal_year_dividend(events, date(2026, 9, 30)) == pytest.approx(.596)


def test_residual_income_with_roe_equal_to_equity_cost_equals_book_value():
    assert intrinsic_value(20, .08, .08, .4, .03, 5) == pytest.approx(20)
    assert intrinsic_value(20, .10, .08, .4, .03, 5) > 20
    assert intrinsic_value(20, .06, .08, .4, .03, 5) < 20


def test_corrected_snapshot_preserves_basis_without_raw_cache(monkeypatch, tmp_path, bank):
    monkeypatch.setattr(bank_base, '_BANK_CACHE_DIR', tmp_path / 'banks')
    cached = bank.model_copy(update={'market_date': date(2026, 9, 30), 'financial_report_date': date(2026, 6, 30),
        'pb_history_dates': [date(2026, 9, 30)], 'pb_history': [.48], 'price_history': [11.57],
        'statistics_version': 2, 'dividend_fiscal_year': 2025, 'dividend_yield_ttm': .073,
        'roe_basis': 'reported_weighted_ordinary_share_annualized',
        'payout_basis': 'annual_dividend_same_year_ordinary_profit',
        'financial_published_date': date(2026, 8, 15), 'data_quality_notes': ['Missing observation'],
        'nim_change': None, 'dividend_stable': None})
    bank_base._write_disk_cache(date(2026, 10, 4), cached)
    restored = bank_base._read_disk_cache(bank.stock_code, date(2026, 10, 4), allow_stale_current=True)
    assert restored.statistics_version == 2
    assert restored.dividend_fiscal_year == 2025
    assert restored.dividend_yield_ttm == .073
    assert restored.roe_basis == cached.roe_basis
    assert restored.payout_basis == cached.payout_basis
    assert restored.financial_published_date == date(2026, 8, 15)
    assert restored.data_quality_notes == ['Missing observation']
    assert restored.nim_change is None
    assert restored.dividend_stable is None


def test_market_date_selects_holiday_snapshot_and_excludes_future_market(monkeypatch, tmp_path, bank):
    from bank_valuation.app.valuation.mean_reversion import _load_latest_cached_bank
    monkeypatch.setattr(bank_base, '_BANK_CACHE_DIR', tmp_path / 'banks')
    cached = bank.model_copy(update={'market_date': date(2026, 9, 30), 'financial_report_date': date(2026, 6, 30),
        'pb_history_dates': [date(2026, 9, 30)], 'pb_history': [.48], 'price_history': [11.57]})
    bank_base._write_disk_cache(date(2026, 10, 4), cached)
    assert _load_latest_cached_bank(bank.stock_code, date(2026, 9, 30)).market_date == date(2026, 9, 30)
    with pytest.raises(RuntimeError, match='估值日期之前'):
        _load_latest_cached_bank(bank.stock_code, date(2026, 9, 29))


def test_benchmark_is_invalidated_after_disclosure_update(monkeypatch, tmp_path, bank):
    from bank_valuation.app.data_sources import industry_benchmark as benchmark
    from bank_valuation.app.data_sources.access_policy import local_data_only
    monkeypatch.setattr(benchmark, '_BENCHMARK_CACHE_DIR', tmp_path / 'benchmarks')
    monkeypatch.setattr(benchmark, '_disclosure_stamp', lambda: 0)
    benchmark._write_csv_cache(date(2026, 9, 30), {'pb': [.5], 'pe': [6], 'roe': [.1], 'profit_growth_yoy': [.03]})
    path = benchmark._cache_path(date(2026, 9, 30))
    with local_data_only():
        assert benchmark._read_csv_cache(date(2026, 9, 30)) is not None
        old_key = benchmark._memory_key(date(2026, 9, 30))
        monkeypatch.setattr(benchmark, '_disclosure_stamp', lambda: path.stat().st_mtime_ns + 1)
        assert benchmark._read_csv_cache(date(2026, 9, 30)) is None
        assert benchmark._memory_key(date(2026, 9, 30)) != old_key
