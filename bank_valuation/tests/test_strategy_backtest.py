from datetime import date

import pytest

from bank_valuation.app.models import BankInput, StrategyBacktestQuery
from bank_valuation.app.valuation import strategy_backtest as backtest


def _bank(code: str) -> BankInput:
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
        pb_history=[1.0, 1.0, 1.0],
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


def _market(prices: dict[date, float]) -> dict[date, backtest.MarketPoint]:
    return {day: backtest.MarketPoint(close=price, pb=1.0) for day, price in prices.items()}


def _data(code: str, prices: dict[date, float], dividends=None) -> backtest.BankBacktestData:
    return backtest.BankBacktestData(_bank(code), _market(prices), dividends or [])


def _query(**overrides) -> StrategyBacktestQuery:
    values = {
        "years": 3,
        "rebalance_frequency": "monthly",
        "holding_count": 3,
        "initial_capital": 100.0,
        "commission_rate": 0.0,
        "stamp_duty_rate": 0.0,
        "transfer_fee_rate": 0.0,
        "slippage_rate": 0.0,
        "cash_yield": 0.0,
    }
    values.update(overrides)
    return StrategyBacktestQuery(**values)


def _result(data, query, monkeypatch, rank_by_date):
    def fake_rank(strategy_id, data, day, query):
        return [
            backtest.Candidate(code=code, name=code, score=100 - index, dividend_yield=0.0, risk_score=0.0)
            for index, code in enumerate(rank_by_date[day])
        ]

    monkeypatch.setattr(backtest, "_rank_candidates", fake_rank)
    return backtest._run_one_strategy(
        "income_core",
        {"name": "test", "description": "test"},
        data,
        sorted(rank_by_date),
        query,
    )


def test_held_price_change_is_counted(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2)]
    data = {
        "A": _data("A", {days[0]: 10.0, days[1]: 11.0}),
        "B": _data("B", {days[0]: 10.0, days[1]: 10.0}),
        "C": _data("C", {days[0]: 10.0, days[1]: 10.0}),
    }
    ranks = {day: ["A", "B", "C"] for day in days}

    result = _result(data, _query(), monkeypatch, ranks)

    assert result.equity_curve[-1].value == 103.333333


def test_holding_profit_tracks_individual_stock_gain(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2)]
    data = {
        "A": _data("A", {days[0]: 10.0, days[1]: 10.0}),
        "B": _data("B", {days[0]: 10.0, days[1]: 20.0}),
        "C": _data("C", {days[0]: 10.0, days[1]: 10.0}),
    }
    ranks = {day: ["A", "B", "C"] for day in days}

    result = _result(data, _query(), monkeypatch, ranks)
    profits = {holding.stock_code: holding.profit for holding in result.holding_snapshots[-1].holdings}

    assert profits["A"] == 0.0
    assert profits["B"] == 33.33
    assert profits["C"] == 0.0


def test_holding_profit_includes_own_cash_dividend(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2)]
    dividend = backtest.DividendEvent(
        report_date=date(2024, 12, 31),
        announcement_date=date(2024, 12, 20),
        ex_dividend_date=days[1],
        cash_per_share=1.0,
    )
    data = {
        "A": _data("A", {day: 10.0 for day in days}, [dividend]),
        "B": _data("B", {day: 10.0 for day in days}),
        "C": _data("C", {day: 10.0 for day in days}),
    }
    ranks = {day: ["A", "B", "C"] for day in days}

    result = _result(data, _query(), monkeypatch, ranks)
    profits = {holding.stock_code: holding.profit for holding in result.holding_snapshots[-1].holdings}

    assert profits["A"] == 3.33
    assert profits["B"] == 0.0
    assert profits["C"] == 0.0


def test_reentered_holding_does_not_capture_gain_while_out_of_portfolio(monkeypatch):
    days = [
        date(2025, 1, 1),
        date(2025, 1, 2),
        date(2025, 2, 1),
        date(2025, 2, 2),
        date(2025, 3, 1),
        date(2025, 3, 2),
    ]
    flat = {day: 10.0 for day in days}
    data = {
        "A": _data(
            "A",
            {
                days[0]: 10.0,
                days[1]: 10.0,
                days[2]: 10.0,
                days[3]: 100.0,
                days[4]: 100.0,
                days[5]: 100.0,
            },
        ),
        "B": _data("B", flat),
        "C": _data("C", flat),
        "D": _data("D", flat),
    }
    ranks = {
        days[0]: ["A", "B", "C", "D"],
        days[1]: ["A", "B", "C", "D"],
        days[2]: ["B", "C", "D", "A"],
        days[3]: ["B", "C", "D", "A"],
        days[4]: ["A", "B", "C", "D"],
        days[5]: ["A", "B", "C", "D"],
    }

    result = _result(data, _query(), monkeypatch, ranks)

    assert result.equity_curve[-1].value == 100.0
    assert max(point.value for point in result.equity_curve) == 100.0


def test_rebalance_cost_is_applied_to_portfolio_value_and_reported(monkeypatch):
    day = date(2025, 1, 1)
    data = {
        code: _data(code, {day: 10.0})
        for code in ["A", "B", "C"]
    }
    ranks = {day: ["A", "B", "C"]}

    result = _result(data, _query(commission_rate=0.01), monkeypatch, ranks)

    assert result.equity_curve[-1].value == 99.0
    assert result.metrics.total_transaction_cost == 1.0


def test_bank_equal_weight_benchmark_averages_daily_returns():
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    data = {
        "A": backtest.BankBacktestData(
            _bank("A"),
            _market({days[0]: 10.0, days[1]: 11.0, days[2]: 12.1}),
            [],
        ),
        "B": backtest.BankBacktestData(
            _bank("B"),
            _market({days[0]: 10.0, days[1]: 9.0, days[2]: 9.9}),
            [],
        ),
    }

    curve = backtest._bank_equal_weight_benchmark(data, days, 100.0)

    assert [point.value for point in curve] == [100.0, 100.0, 110.0]


def test_cash_dividend_is_counted_only_on_ex_dividend_date(monkeypatch):
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    dividend = backtest.DividendEvent(
        report_date=date(2024, 12, 31),
        announcement_date=date(2024, 12, 20),
        ex_dividend_date=days[1],
        cash_per_share=1.0,
    )
    data = {
        "A": _data("A", {day: 10.0 for day in days}, [dividend]),
        "B": _data("B", {day: 10.0 for day in days}),
        "C": _data("C", {day: 10.0 for day in days}),
    }
    ranks = {day: ["A", "B", "C"] for day in days}

    result = _result(data, _query(), monkeypatch, ranks)

    assert result.equity_curve[-1].value == 103.333333
    assert result.metrics.annual_dividend_return > 0


def test_missing_dividend_history_does_not_fall_back_to_current_bank_value():
    item = _data("A", {date(2026, 7, 10): 10.0}, [])
    item.bank.dividend_per_share = 9.99

    assert backtest._historical_dividend_yield(item, 10.0, date(2026, 7, 10)) is None


def test_duplicate_dividend_event_is_not_counted_twice():
    day = date(2026, 7, 10)
    event = backtest.DividendEvent(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 3, 20),
        ex_dividend_date=day,
        cash_per_share=0.5,
    )
    duplicate = backtest.DividendEvent(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 3, 25),
        ex_dividend_date=day,
        cash_per_share=0.5,
    )
    item = _data("A", {day: 10.0}, [event, duplicate])

    assert backtest._cash_dividend_on_day(item, day) == pytest.approx(0.5)
    assert backtest._historical_dividend_yield(item, 10.0, day) == pytest.approx(0.05)


def test_dividend_events_are_cached(monkeypatch, tmp_path):
    path = tmp_path / "A_dividend_events.csv"
    events = [
        backtest.DividendEvent(
            report_date=date(2024, 12, 31),
            announcement_date=date(2025, 3, 30),
            ex_dividend_date=date(2025, 7, 1),
            cash_per_share=0.5,
        )
    ]

    monkeypatch.setattr(backtest, "_dividend_events_path", lambda code: path)
    monkeypatch.setattr(backtest, "_fetch_dividend_events", lambda code: events)

    first = backtest._read_dividend_events("A")
    assert first == events
    assert path.exists()

    monkeypatch.setattr(backtest, "_fetch_dividend_events", lambda code: (_ for _ in ()).throw(AssertionError("should use cache")))
    second = backtest._read_dividend_events("A")

    assert second == events


def test_explicit_near_one_year_range_is_allowed(monkeypatch):
    days = [date(2025, 1, 1).replace() for _ in range(1)]
    days = [date.fromordinal(date(2025, 1, 1).toordinal() + index) for index in range(240)]
    data = {"A": _data("A", {day: 10.0 for day in days})}

    def fake_strategy(strategy_id, config, data, dates, query):
        assert len(dates) == 240
        return backtest.StrategyBacktestResult(
            strategy_id=strategy_id,
            strategy_name="test",
            description="test",
            metrics=backtest.BacktestMetrics(
                total_return=0.0,
                annualized_return=0.0,
                max_drawdown=0.0,
                max_drawdown_date=dates[0],
                recovery_date=dates[-1],
                recovery_days=0,
                volatility=0.0,
                sharpe=None,
                calmar=None,
                win_year_rate=0.0,
                annual_dividend_return=0.0,
                turnover=0.0,
                rebalance_count=0,
                total_transaction_cost=0.0,
            ),
            equity_curve=[backtest.BacktestPoint(date=dates[0], value=100.0), backtest.BacktestPoint(date=dates[-1], value=100.0)],
            drawdown_curve=[backtest.BacktestPoint(date=dates[0], value=0.0), backtest.BacktestPoint(date=dates[-1], value=0.0)],
            yearly_returns=[],
            current_holdings=[],
        )

    monkeypatch.setattr(backtest, "_load_backtest_data", lambda: data)
    monkeypatch.setattr(backtest, "_run_one_strategy", fake_strategy)

    response = backtest.run_strategy_backtest(_query(start_date=days[0], end_date=days[-1]))

    assert response.start_date == days[0]
    assert response.end_date == days[-1]


def test_too_short_explicit_range_reports_available_days(monkeypatch):
    days = [date.fromordinal(date(2025, 1, 1).toordinal() + index) for index in range(30)]
    monkeypatch.setattr(backtest, "_load_backtest_data", lambda: {"A": _data("A", {day: 10.0 for day in days})})

    try:
        backtest.run_strategy_backtest(_query(start_date=days[0], end_date=days[-1]))
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected short date range to fail")

    assert "30 个交易日" in message
    assert "至少需要" in message
