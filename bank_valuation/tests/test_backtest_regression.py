"""Deterministic accounting regressions for the two backtest engines.

These tests deliberately use tiny hand-calculable ledgers.  They protect the
financial identities and boundary rules that broad API tests can miss.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from bank_valuation.app.data_sources.industry_catalog import profiles_for_industries
from bank_valuation.app.data_sources.security_history import SecurityMarketPoint
from bank_valuation.app.models import (
    BacktestPoint,
    BankInput,
    CrossIndustryStrategyBacktestQuery,
    StrategyBacktestQuery,
)
from bank_valuation.app.valuation import cross_industry_backtest as cross
from bank_valuation.app.valuation import strategy_backtest as bank


def _bank_input(code: str) -> BankInput:
    return BankInput(
        stock_code=code,
        stock_name=code,
        current_price=10.0,
        bps=10.0,
        eps=1.0,
        roe=0.1,
        net_profit=1_000_000_000,
        profit_growth_yoy=0.02,
        dividend_per_share=0.0,
        payout_ratio=0.0,
        dividend_yield=0.0,
        pb_current=1.0,
        pe_current=10.0,
        pb_history=[1.0],
        nim=0.02,
        npl_ratio=0.01,
        provision_coverage=2.0,
        cet1_ratio=0.12,
        capital_adequacy_ratio=0.15,
        risk_free_rate=0.02,
        equity_risk_premium=0.06,
        beta=0.8,
        long_term_growth=0.02,
    )


def _bank_data(
    code: str,
    days: list[date],
    prices: list[float],
    dividends: list[bank.DividendEvent] | None = None,
) -> bank.BankBacktestData:
    return bank.BankBacktestData(
        bank=_bank_input(code),
        market={
            day: bank.MarketPoint(close=price, pb=1.0)
            for day, price in zip(days, prices, strict=True)
        },
        dividends=dividends or [],
    )


def _bank_query(**overrides) -> StrategyBacktestQuery:
    values = {
        "years": 3,
        "rebalance_frequency": "monthly",
        "holding_count": 3,
        "initial_capital": 1_000.0,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "transfer_fee_rate": 0.0,
        "slippage_rate": 0.0,
        "cash_yield": 0.0,
    }
    values.update(overrides)
    return StrategyBacktestQuery(**values)


def _candidate(code: str, *, score: float = 90.0, industry: str = "telecom") -> cross.CrossCandidate:
    return cross.CrossCandidate(
        code=code,
        name=code,
        industry_id=industry,
        score=score,
        dividend_yield=0.05,
        risk_score=10.0,
        volatility=0.1,
    )


def _assert_holding_ledger_balances(result) -> None:
    final_value = result.equity_curve[-1].value
    holdings = result.current_holdings
    assert sum(item.position_value for item in holdings) <= final_value + 0.02
    for holding in holdings:
        assert holding.profit == pytest.approx(
            holding.price_profit + holding.dividend_profit,
            abs=0.02,
        )
        assert holding.position_value == pytest.approx(
            holding.cost_basis + holding.profit,
            abs=0.02,
        )
    for series in result.holding_price_series:
        values = [point.value for point in series.price_curve]
        assert series.current_price == values[-1]
        assert series.high_price >= max(values)
        assert series.low_price <= min(values)
        assert series.low_price <= series.entry_price <= series.high_price


def test_bank_golden_ledger_balances_price_dividend_weights_and_profit(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2)]
    dividend = bank.DividendEvent(
        report_date=date(2024, 12, 31),
        announcement_date=date(2024, 12, 20),
        ex_dividend_date=days[1],
        cash_per_share=1.0,
    )
    data = {
        "A": _bank_data("A", days, [10.0, 12.0]),
        "B": _bank_data("B", days, [10.0, 9.0]),
        "C": _bank_data("C", days, [10.0, 10.0], [dividend]),
    }
    monkeypatch.setattr(
        bank,
        "_rank_candidates",
        lambda *_args: [
            bank.Candidate(code=code, name=code, score=100 - index, dividend_yield=0.05, risk_score=10)
            for index, code in enumerate(["A", "B", "C"])
        ],
    )

    result = bank._run_one_strategy(
        "income_core",
        {"name": "golden", "description": "golden"},
        data,
        days,
        _bank_query(),
    )

    # Independent ledger: (20% - 10% + 10% dividend) / 3 = 6.6667%.
    assert [point.value for point in result.equity_curve] == pytest.approx([1_000.0, 1_066.666667])
    assert result.metrics.total_return == pytest.approx(0.066667)
    holdings = {item.stock_code: item for item in result.current_holdings}
    assert {code: item.weight for code, item in holdings.items()} == pytest.approx(
        {"A": 0.375, "B": 0.28125, "C": 0.34375}
    )
    assert holdings["A"].profit == pytest.approx(66.67, abs=0.01)
    assert holdings["B"].profit == pytest.approx(-33.33, abs=0.01)
    assert holdings["C"].price_profit == 0.0
    assert holdings["C"].dividend_profit == pytest.approx(33.33, abs=0.01)
    assert sum(item.profit for item in holdings.values()) == pytest.approx(66.67, abs=0.02)
    contribution = result.total_profit_contribution
    assert contribution is not None
    assert contribution.net_profit == pytest.approx(66.67, abs=0.01)
    assert contribution.stock_net_profit == pytest.approx(66.67, abs=0.01)
    assert contribution.cash_profit == 0.0
    assert contribution.reconciliation_error == pytest.approx(0.0, abs=1e-5)
    contribution_by_code = {item.stock_code: item for item in contribution.stocks}
    assert contribution_by_code["A"].net_profit == pytest.approx(66.67, abs=0.01)
    assert contribution_by_code["B"].net_profit == pytest.approx(-33.33, abs=0.01)
    assert contribution_by_code["C"].price_profit == 0.0
    assert contribution_by_code["C"].dividend_profit == pytest.approx(33.33, abs=0.01)
    assert contribution_by_code["C"].return_contribution == pytest.approx(1 / 30, abs=1e-6)
    _assert_holding_ledger_balances(result)


def test_bank_total_return_includes_initial_entry_cost(monkeypatch):
    day = date(2025, 1, 1)
    data = {code: _bank_data(code, [day], [10.0]) for code in ["A", "B", "C"]}
    monkeypatch.setattr(
        bank,
        "_rank_candidates",
        lambda *_args: [bank.Candidate(code=code, name=code, score=90, dividend_yield=0.05, risk_score=10) for code in data],
    )

    result = bank._run_one_strategy(
        "income_core",
        {"name": "cost", "description": "cost"},
        data,
        [day],
        _bank_query(commission_rate=0.01),
    )

    assert result.equity_curve[-1].value == 990.0
    assert result.metrics.total_transaction_cost == 10.0
    assert result.metrics.total_return == pytest.approx(-0.01)
    assert [(item.year, item.return_rate) for item in result.yearly_returns] == [(2025, -0.01)]


def test_bank_full_replacement_charges_buy_sell_and_stamp_costs(monkeypatch):
    days = [date(2024, 12, 31), date(2025, 1, 1)]
    codes = ["A", "B", "C", "D", "E", "F"]
    data = {code: _bank_data(code, days, [10.0, 10.0]) for code in codes}

    def rank(_strategy_id, _data, day, _query):
        selected = codes[:3] if day == days[0] else codes[3:]
        return [bank.Candidate(code=code, name=code, score=90, dividend_yield=0.05, risk_score=10) for code in selected]

    monkeypatch.setattr(bank, "_rank_candidates", rank)
    result = bank._run_one_strategy(
        "income_core",
        {"name": "cost", "description": "cost"},
        data,
        days,
        _bank_query(commission_rate=0.01, stamp_duty_rate=0.02),
    )

    # Entry costs 1%; full replacement turnover is 2 and sell turnover is 1,
    # so the second rebalance costs another 4% of the then-current 990.
    assert [point.value for point in result.equity_curve] == pytest.approx([990.0, 950.4])
    assert result.metrics.turnover == pytest.approx(3.0)
    assert result.metrics.total_transaction_cost == pytest.approx(49.6)
    assert result.metrics.total_return == pytest.approx(-0.0496)
    assert {item.stock_code for item in result.current_holdings} == {"D", "E", "F"}
    total = result.total_profit_contribution
    assert total is not None
    assert total.net_profit == pytest.approx(-49.6)
    assert total.stock_net_profit == pytest.approx(-49.6)
    assert total.cash_profit == 0.0
    assert total.reconciliation_error == pytest.approx(0.0, abs=1e-5)
    assert {item.stock_code for item in total.stocks} == set(codes)
    assert sum(item.transaction_cost for item in total.stocks) == pytest.approx(49.6, abs=0.02)
    yearly = {item.year: item for item in result.yearly_profit_contributions}
    assert set(yearly) == {2024, 2025}
    assert yearly[2024].net_profit == pytest.approx(-10.0)
    assert yearly[2024].return_rate == pytest.approx(-0.01)
    assert {item.stock_code for item in yearly[2024].stocks} == {"A", "B", "C"}
    assert yearly[2025].net_profit == pytest.approx(-39.6)
    assert yearly[2025].return_rate == pytest.approx(-0.04)
    assert {item.stock_code for item in yearly[2025].stocks} == set(codes)
    assert all(item.reconciliation_error == pytest.approx(0.0, abs=1e-5) for item in yearly.values())


def test_yearly_return_uses_previous_year_close_as_next_year_base():
    equity = [
        BacktestPoint(date=date(2024, 12, 31), value=100.0),
        BacktestPoint(date=date(2025, 1, 2), value=110.0),
        BacktestPoint(date=date(2025, 12, 31), value=121.0),
    ]

    yearly = bank._yearly_returns(equity)

    assert [(item.year, item.return_rate) for item in yearly] == [(2025, pytest.approx(0.21))]


def test_metrics_lock_max_drawdown_and_recovery_date():
    days = [date(2025, 1, day) for day in range(1, 5)]
    equity = [BacktestPoint(date=day, value=value) for day, value in zip(days, [100.0, 80.0, 90.0, 100.0], strict=True)]
    drawdown = [BacktestPoint(date=day, value=value) for day, value in zip(days, [0.0, -0.2, -0.1, 0.0], strict=True)]

    metrics = bank._metrics(
        equity=equity,
        drawdown=drawdown,
        daily_returns=[0.0, -0.2, 0.125, 1 / 9],
        cash_yield=0.0,
        dividend_gain=0.0,
        total_turnover=0.0,
        total_transaction_cost=0.0,
        rebalance_count=0,
        initial_value=100.0,
    )

    assert metrics.total_return == 0.0
    assert metrics.max_drawdown == -0.2
    assert metrics.max_drawdown_date == days[1]
    assert metrics.recovery_date == days[3]
    assert metrics.recovery_days == 2


def test_bank_valuation_factors_ignore_future_pb_values():
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    market = {
        days[0]: bank.MarketPoint(close=10.0, pb=1.0),
        days[1]: bank.MarketPoint(close=10.0, pb=2.0),
        days[2]: bank.MarketPoint(close=10.0, pb=100.0),
    }

    assert bank._pb_percentile(market, days[1], current_pb=2.0, years=5) == 1.0
    assert bank._median_pb_until(market, days[1], years=5) == pytest.approx(1.5)


def test_bank_real_ranking_filters_on_historical_dividend_yield():
    day = date(2025, 7, 1)
    data = {
        "HIGH": _bank_data(
            "HIGH",
            [day],
            [10.0],
            [bank.DividendEvent(date(2024, 12, 31), date(2025, 3, 1), day, 0.8)],
        ),
        "LOW": _bank_data(
            "LOW",
            [day],
            [10.0],
            [bank.DividendEvent(date(2024, 12, 31), date(2025, 3, 1), day, 0.2)],
        ),
    }

    candidates = bank._rank_candidates(
        "income_core",
        data,
        day,
        _bank_query(
            min_dividend_yield=0.05,
            min_dividend_safety=0.0,
            min_stable_growth=0.0,
            max_risk_score=100.0,
            max_payout_ratio=1.5,
        ),
    )

    assert [item.code for item in candidates] == ["HIGH"]
    assert candidates[0].dividend_yield == pytest.approx(0.08)


def test_bank_missing_trading_day_carries_last_price_without_phantom_return(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    data = {
        "A": _bank_data("A", [days[0], days[2]], [10.0, 11.0]),
        "B": _bank_data("B", days, [10.0, 10.0, 10.0]),
        "C": _bank_data("C", days, [10.0, 10.0, 10.0]),
    }
    monkeypatch.setattr(
        bank,
        "_rank_candidates",
        lambda *_args: [bank.Candidate(code=code, name=code, score=90, dividend_yield=0.05, risk_score=10) for code in data],
    )

    result = bank._run_one_strategy(
        "income_core",
        {"name": "missing", "description": "missing"},
        data,
        days,
        _bank_query(),
    )

    assert [point.value for point in result.equity_curve] == pytest.approx(
        [1_000.0, 1_000.0, 1_033.333333]
    )


def test_cross_target_weights_lock_industry_slots_and_selected_names():
    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="selected",
        industry_ids=["telecom", "hydro"],
        industry_weighting="equal",
        max_industry_weight=0.45,
        crisis_cash_buffer=0.1,
        holding_count=3,
    )
    candidates = [
        _candidate("T1", score=100, industry="telecom"),
        _candidate("T2", score=90, industry="telecom"),
        _candidate("H1", score=95, industry="hydro"),
        _candidate("H2", score=80, industry="hydro"),
    ]

    weights = cross._target_weights("income_core", candidates, query)

    assert weights == pytest.approx({"T1": 0.225, "T2": 0.225, "H1": 0.45})
    assert sum(weights.values()) == pytest.approx(0.9)


def test_cross_real_ranking_filters_on_as_of_dividend_data():
    days = [date(2025, 1, 1) + timedelta(days=index) for index in range(60)]
    base_profile = profiles_for_industries(["telecom"])[0]

    def item(code: str, cash: float) -> cross.CrossBacktestData:
        market = {
            day: SecurityMarketPoint(close=10.0, pb=1.0, pe=10.0)
            for day in days
        }
        return cross.CrossBacktestData(
            profile=replace(base_profile, code=code, name=code),
            market=market,
            raw_market=market,
            dividends=[
                cross.DividendEvent(
                    report_date=date(2024, 12, 31),
                    announcement_date=date(2025, 1, 10),
                    ex_dividend_date=days[-1],
                    cash_per_share=cash,
                )
            ],
            dividend_data_available=True,
        )

    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="single",
        industry_ids=["telecom"],
        min_dividend_yield=0.03,
        min_dividend_safety=0.0,
        min_stable_growth=0.0,
        max_risk_score=100.0,
    )
    candidates = cross._rank_candidates(
        "income_core",
        {"HIGH": item("HIGH", 0.5), "LOW": item("LOW", 0.1)},
        days[-1],
        query,
    )

    assert [candidate.code for candidate in candidates] == ["HIGH"]
    assert candidates[0].dividend_yield == pytest.approx(0.05)


def test_cross_full_replacement_charges_the_complete_cost_schedule(monkeypatch):
    days = [date(2025, 1, 31), date(2025, 2, 1)]
    codes = ["A", "B", "C", "D", "E", "F"]
    profile = profiles_for_industries(["telecom"])[0]
    data = {}
    for code in codes:
        market = {day: SecurityMarketPoint(close=10.0, pb=1.0, pe=10.0) for day in days}
        data[code] = cross.CrossBacktestData(
            profile=replace(profile, code=code, name=code),
            market=market,
            raw_market=market,
            dividends=[],
            dividend_data_available=True,
        )

    def rank(_strategy_id, _data, day, _query):
        selected = codes[:3] if day == days[0] else codes[3:]
        return [_candidate(code) for code in selected]

    monkeypatch.setattr(cross, "_rank_candidates", rank)
    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="single",
        industry_ids=["telecom"],
        crisis_cash_buffer=0.0,
        holding_count=3,
        rebalance_frequency="monthly",
        initial_capital=1_000.0,
        commission_rate=0.01,
        stamp_duty_rate=0.02,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
        cash_yield=0.0,
    )
    result = cross._run_one_strategy(
        "income_core",
        {"name": "cost", "description": "cost"},
        data,
        days,
        query,
    )

    assert [point.value for point in result.equity_curve] == pytest.approx([990.0, 950.4])
    assert result.metrics.turnover == pytest.approx(3.0)
    assert result.metrics.total_transaction_cost == pytest.approx(49.6)
    assert result.metrics.total_return == pytest.approx(-0.0496)
    assert {item.stock_code for item in result.current_holdings} == {"D", "E", "F"}
    total = result.total_profit_contribution
    assert total is not None
    assert total.net_profit == pytest.approx(-49.6)
    assert total.stock_net_profit == pytest.approx(-49.6)
    assert total.reconciliation_error == pytest.approx(0.0, abs=1e-5)
    assert {item.stock_code for item in total.stocks} == set(codes)


def test_cross_benchmark_equal_weights_industries_before_stocks():
    days = [date(2025, 1, 1), date(2025, 1, 2)]

    def item(code: str, industry: str, prices: list[float]) -> cross.CrossBacktestData:
        profile = replace(profiles_for_industries([industry])[0], code=code, name=code)
        market = {
            day: SecurityMarketPoint(close=price, pb=1.0, pe=10.0)
            for day, price in zip(days, prices, strict=True)
        }
        return cross.CrossBacktestData(profile, market, market, [], True)

    data = {
        "T1": item("T1", "telecom", [10.0, 11.0]),
        "T2": item("T2", "telecom", [10.0, 9.0]),
        "H1": item("H1", "hydro", [10.0, 12.0]),
    }

    curve = cross._industry_equal_weight_benchmark(data, days, 100.0)

    # Telecom's +10%/-10% average is 0%; hydro is +20%; industry equal weight is +10%.
    assert [point.value for point in curve] == pytest.approx([100.0, 110.0])


def test_cross_golden_ledger_separates_post_adjusted_return_from_raw_price(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2)]
    profile = replace(profiles_for_industries(["telecom"])[0], code="A", name="A")
    dividend = cross.DividendEvent(
        report_date=date(2024, 12, 31),
        announcement_date=date(2024, 12, 20),
        ex_dividend_date=days[1],
        cash_per_share=0.5,
    )
    item = cross.CrossBacktestData(
        profile=profile,
        market={
            days[0]: SecurityMarketPoint(close=100.0, pb=1.0, pe=10.0),
            days[1]: SecurityMarketPoint(close=110.0, pb=1.0, pe=10.0),
        },
        raw_market={
            days[0]: SecurityMarketPoint(close=10.0, pb=1.0, pe=10.0),
            days[1]: SecurityMarketPoint(close=10.5, pb=1.0, pe=10.0),
        },
        dividends=[dividend],
        dividend_data_available=True,
    )
    monkeypatch.setattr(cross, "_rank_candidates", lambda *_args: [_candidate("A")])
    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="single",
        industry_ids=["telecom"],
        crisis_cash_buffer=0.1,
        holding_count=3,
        start_date=days[0],
        end_date=days[-1],
        min_dividend_yield=0.0,
        min_dividend_safety=0.0,
        min_stable_growth=0.0,
        max_risk_score=100.0,
        initial_capital=1_000.0,
        commission_rate=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
        cash_yield=0.0,
    )

    result = cross._run_one_strategy(
        "income_core",
        {"name": "golden", "description": "golden"},
        {"A": item},
        days,
        query,
    )

    # 90% invested * 10% post-adjusted total return = 9% portfolio return.
    assert [point.value for point in result.equity_curve] == pytest.approx([1_000.0, 1_090.0])
    assert result.metrics.total_return == pytest.approx(0.09)
    holding = result.current_holdings[0]
    assert holding.position_value == 990.0
    assert holding.cost_basis == 900.0
    assert holding.profit == 90.0
    assert holding.price_profit == 45.0
    assert holding.dividend_profit == 45.0
    series = result.holding_price_series[0]
    assert series.entry_price == 10.0
    assert series.current_price == 10.5
    assert series.price_return == pytest.approx(0.05)
    contribution = result.total_profit_contribution
    assert contribution is not None
    assert contribution.net_profit == 90.0
    assert contribution.stock_net_profit == 90.0
    assert contribution.cash_profit == 0.0
    assert contribution.reconciliation_error == pytest.approx(0.0, abs=1e-5)
    stock = contribution.stocks[0]
    assert stock.stock_code == "A"
    assert stock.price_profit == 45.0
    assert stock.dividend_profit == 45.0
    assert stock.transaction_cost == 0.0
    assert stock.net_profit == 90.0
    assert stock.return_contribution == pytest.approx(0.09)
    _assert_holding_ledger_balances(result)
