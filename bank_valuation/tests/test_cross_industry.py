from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bank_valuation.app.data_sources.industry_catalog import profiles_for_industries
from bank_valuation.app.data_sources.security_history import MarketHistoryLoad, SecurityMarketPoint
from bank_valuation.app.data_sources import security_history as history_source
from bank_valuation.app.main import app
from bank_valuation.app.models import ALL_INDUSTRY_IDS, CrossIndustryStrategyBacktestQuery
from bank_valuation.app.valuation import cross_industry_backtest as cross


def _days(count: int) -> list[date]:
    first = date(2025, 1, 1)
    return [first + timedelta(days=index) for index in range(count)]


def _histories(industry_ids: list[str], days: list[date]) -> dict[str, dict[date, SecurityMarketPoint]]:
    histories: dict[str, dict[date, SecurityMarketPoint]] = {}
    for profile_index, profile in enumerate(profiles_for_industries(industry_ids)):
        price = 10.0 + profile_index
        histories[profile.code] = {
            day: SecurityMarketPoint(close=price, pb=1.0, pe=10.0)
            for day in days
        }
    return histories


def _query(
    industry_ids: list[str],
    days: list[date],
    *,
    universe_mode: str | None = None,
    max_industry_weight: float | None = None,
    crisis_cash_buffer: float = 0.1,
    holding_count: int | None = None,
) -> CrossIndustryStrategyBacktestQuery:
    mode = universe_mode or ("single" if len(industry_ids) == 1 else "selected")
    cap = max_industry_weight
    if cap is None:
        cap = 1.0 if len(industry_ids) == 1 else (1 - crisis_cash_buffer) / len(industry_ids)
    return CrossIndustryStrategyBacktestQuery(
        universe_mode=mode,
        industry_ids=industry_ids,
        industry_weighting="equal",
        max_industry_weight=cap,
        crisis_cash_buffer=crisis_cash_buffer,
        holding_count=holding_count or max(3, len(industry_ids)),
        start_date=days[0],
        end_date=days[-1],
        min_dividend_yield=0.0,
        min_dividend_safety=0.0,
        min_stable_growth=0.0,
        max_risk_score=100.0,
        commission_rate=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
        slippage_rate=0.0,
        cash_yield=0.0,
        initial_capital=100.0,
    )


def _install_history_loader(monkeypatch, histories, failures=None) -> None:
    def fake_loader(_profiles, _start_date, _end_date, **_kwargs):
        return MarketHistoryLoad(histories=histories, failures=failures or [])

    monkeypatch.setattr(cross, "load_market_histories", fake_loader)
    monkeypatch.setattr(
        cross,
        "_load_dividend_histories",
        lambda profiles, **_kwargs: ({profile.code: [] for profile in profiles}, []),
    )


def test_cross_industry_query_validates_single_and_selected_modes():
    single = CrossIndustryStrategyBacktestQuery(
        universe_mode="single",
        industry_ids=["telecom"],
        max_industry_weight=0.1,
        crisis_cash_buffer=0.3,
    )
    assert single.industry_ids == ["telecom"]

    selected = CrossIndustryStrategyBacktestQuery(
        universe_mode="selected",
        industry_ids=["telecom", "hydro", "bank"],
        max_industry_weight=0.3,
        crisis_cash_buffer=0.1,
    )
    assert selected.industry_ids == ["telecom", "hydro", "bank"]

    for invalid_ids in ([], ["telecom", "hydro"]):
        with pytest.raises(ValidationError):
            CrossIndustryStrategyBacktestQuery(
                universe_mode="single",
                industry_ids=invalid_ids,
            )

    with pytest.raises(ValidationError):
        CrossIndustryStrategyBacktestQuery(
            universe_mode="selected",
            industry_ids=[],
        )


def test_cross_industry_query_all_mode_canonicalizes_the_universe():
    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="all",
        industry_ids=[],
        max_industry_weight=0.2,
        holding_count=40,
    )

    assert query.industry_ids == list(ALL_INDUSTRY_IDS)


def test_cross_industry_query_rejects_unknown_industry():
    with pytest.raises(ValidationError):
        CrossIndustryStrategyBacktestQuery(
            universe_mode="single",
            industry_ids=["unknown"],
        )


def test_cross_industry_query_rejects_infeasible_cap():
    with pytest.raises(ValidationError):
        CrossIndustryStrategyBacktestQuery(
            universe_mode="selected",
            industry_ids=["telecom", "hydro", "bank"],
            max_industry_weight=0.2,
            crisis_cash_buffer=0.1,
        )


@pytest.mark.parametrize("holding_count", [2, 41])
def test_cross_industry_query_rejects_holding_count_outside_generic_limits(holding_count):
    with pytest.raises(ValidationError):
        CrossIndustryStrategyBacktestQuery(
            universe_mode="single",
            industry_ids=["telecom"],
            holding_count=holding_count,
        )


def test_cross_industry_query_accepts_generic_holding_count_upper_bound():
    query = CrossIndustryStrategyBacktestQuery(
        universe_mode="single",
        industry_ids=["telecom"],
        holding_count=40,
    )

    assert query.holding_count == 40


def test_strategy_universe_endpoint_returns_all_catalogued_industries():
    response = TestClient(app).get("/api/strategy/universe")

    assert response.status_code == 200
    payload = response.json()
    assert payload["industry_count"] == len(ALL_INDUSTRY_IDS)
    assert payload["stock_count"] == 41
    assert [item["industry_id"] for item in payload["industries"]] == list(ALL_INDUSTRY_IDS)
    assert all(item["stocks"] for item in payload["industries"])
    hydro = next(item for item in payload["industries"] if item["industry_id"] == "hydro")
    assert len(hydro["stocks"]) == 6
    assert {item["stock_code"] for item in hydro["stocks"]} >= {"600236", "002039"}
    consumer = next(item for item in payload["industries"] if item["industry_id"] == "consumer")
    assert len(consumer["stocks"]) == 10
    assert {item["stock_code"] for item in consumer["stocks"]} >= {"000568", "600600", "603195", "002032"}


def test_industry_analysis_endpoint_passes_the_validated_query(monkeypatch):
    captured = []

    def fake_analysis(query):
        captured.append(query)
        return {
            "module": "industry_analysis",
            "industry_id": query.industry_id,
            "stock_code": query.stock_code,
        }

    monkeypatch.setattr("bank_valuation.app.routers.strategy.analyze_industry_stock", fake_analysis)
    response = TestClient(app).post(
        "/api/industry/analysis",
        json={
            "industry_id": "telecom",
            "stock_code": "600941",
            "valuation_date": "2025-06-10",
        },
    )

    assert response.status_code == 200
    assert response.json()["module"] == "industry_analysis"
    assert captured[0].industry_id == "telecom"
    assert captured[0].stock_code == "600941"
    assert captured[0].valuation_date == date(2025, 6, 10)


def test_cross_industry_backtest_endpoint_passes_universe_controls(monkeypatch):
    captured = []

    def fake_backtest(query, *, refresh_cache=False):
        captured.append((query, refresh_cache))
        return {
            "module": "cross_industry_strategy_backtest",
            "selected_industry_ids": query.industry_ids,
            "strategy_count": 0,
            "results": [],
        }

    monkeypatch.setattr("bank_valuation.app.routers.strategy.run_cross_industry_backtest", fake_backtest)
    response = TestClient(app).post(
        "/api/strategy/backtest",
        json={
            "universe_mode": "selected",
            "industry_ids": ["telecom", "hydro"],
            "industry_weighting": "score",
            "max_industry_weight": 0.45,
            "crisis_cash_buffer": 0.1,
            "holding_count": 20,
        },
    )

    assert response.status_code == 200
    assert response.json()["selected_industry_ids"] == ["telecom", "hydro"]
    query, refresh_cache = captured[0]
    assert query.universe_mode == "selected"
    assert query.industry_weighting == "score"
    assert query.max_industry_weight == pytest.approx(0.45)
    assert query.crisis_cash_buffer == pytest.approx(0.1)
    assert query.holding_count == 20
    assert refresh_cache is False


def test_capped_weights_redistribute_excess_without_exceeding_cap():
    weights = cross._capped_weights(
        {"telecom": 9.0, "hydro": 1.0, "bank": 1.0},
        total_weight=0.9,
        cap=0.4,
    )

    assert weights == pytest.approx({"telecom": 0.4, "hydro": 0.25, "bank": 0.25})
    assert sum(weights.values()) == pytest.approx(0.9)
    assert max(weights.values()) <= 0.4


def test_rebalance_cost_basis_adds_new_cash_without_rebooking_old_profit():
    bases = cross._rebalance_position_cost_bases(
        {"sh.600938": 80_000.0},
        {"sh.600938": 0.10},
        {"sh.600938": 0.10},
        portfolio_value_before_cost=1_000_000.0,
        portfolio_value_after_cost=1_100_000.0,
    )

    # The old position has 20,000 unrealized profit. Raising the target value
    # from 100,000 to 110,000 adds 10,000 of cost, not 100,000 of fake profit.
    assert bases["sh.600938"] == pytest.approx(90_000.0)
    assert 110_000.0 - bases["sh.600938"] == pytest.approx(20_000.0)


def test_rebalance_cost_basis_releases_cost_proportionally_on_partial_sale():
    bases = cross._rebalance_position_cost_bases(
        {"sh.600938": 100_000.0},
        {"sh.600938": 0.12},
        {"sh.600938": 0.09},
        portfolio_value_before_cost=1_000_000.0,
        portfolio_value_after_cost=1_000_000.0,
    )

    # Selling one quarter of a 120,000 position also releases one quarter of
    # its 100,000 cost. The remaining position therefore has 15,000 profit.
    assert bases["sh.600938"] == pytest.approx(75_000.0)
    assert 90_000.0 - bases["sh.600938"] == pytest.approx(15_000.0)


def test_china_oil_like_total_return_cannot_create_oversized_current_profit():
    entry_value = 100_000.0
    total_return = 36.87016625 / 33.2431906 - 1
    current_value = entry_value * (1 + total_return)

    assert total_return == pytest.approx(0.1091, abs=0.0001)
    assert current_value - entry_value == pytest.approx(10_910, abs=20)
    assert current_value - entry_value < 65_000


def test_trailing_dividend_yield_uses_implemented_ttm_cash_once():
    profile = profiles_for_industries(["oilgas"])[0]
    day = date(2026, 7, 10)
    event = cross.DividendEvent(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 3, 20),
        ex_dividend_date=date(2026, 7, 10),
        cash_per_share=0.5,
    )
    duplicate = cross.DividendEvent(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 3, 25),
        ex_dividend_date=date(2026, 7, 10),
        cash_per_share=0.5,
    )
    item = cross.CrossBacktestData(
        profile=profile,
        market={day: SecurityMarketPoint(close=36.0, pb=1.0, pe=10.0)},
        raw_market={day: SecurityMarketPoint(close=25.0, pb=1.0, pe=10.0)},
        dividends=cross._deduplicate_dividend_events([event, duplicate]),
        dividend_data_available=True,
    )

    assert len(item.dividends) == 1
    assert cross._trailing_dividend_yield(item, day) == pytest.approx(0.02)
    assert cross._cash_dividend_on_day(item.dividends, day) == pytest.approx(0.5)


def test_trailing_dividend_yield_returns_none_when_source_is_unavailable():
    profile = profiles_for_industries(["oilgas"])[0]
    day = date(2026, 7, 10)
    item = cross.CrossBacktestData(
        profile=profile,
        market={day: SecurityMarketPoint(close=36.0, pb=1.0, pe=10.0)},
        raw_market={day: SecurityMarketPoint(close=25.0, pb=1.0, pe=10.0)},
        dividends=[],
        dividend_data_available=False,
    )

    assert cross._trailing_dividend_yield(item, day) is None


def test_cross_industry_engine_respects_selected_industries_and_cap(monkeypatch):
    days = _days(130)
    selected = ["telecom", "hydro", "bank"]
    _install_history_loader(monkeypatch, _histories(selected, days))
    query = _query(
        selected,
        days,
        max_industry_weight=0.3,
        crisis_cash_buffer=0.1,
        holding_count=9,
    )

    response = cross.run_cross_industry_backtest(query)

    assert response.selected_industry_ids == selected
    assert set(response.industry_allocation) == set(selected)
    assert sum(response.industry_allocation.values()) == pytest.approx(0.9, abs=1e-6)
    assert max(response.industry_allocation.values()) <= 0.3 + 1e-6
    for result in response.results:
        assert {holding.industry_id for holding in result.current_holdings} == set(selected)
        for snapshot in result.holding_snapshots:
            assert {holding.industry_id for holding in snapshot.holdings} <= set(selected)


def test_cross_industry_engine_keeps_the_requested_cash_buffer(monkeypatch):
    days = _days(130)
    selected = ["telecom"]
    _install_history_loader(monkeypatch, _histories(selected, days))
    query = _query(
        selected,
        days,
        max_industry_weight=0.1,
        crisis_cash_buffer=0.2,
        holding_count=3,
    )

    response = cross.run_cross_industry_backtest(query)

    assert response.industry_allocation == pytest.approx({"telecom": 0.8}, abs=2e-6)
    for result in response.results:
        assert sum(holding.weight for holding in result.current_holdings) == pytest.approx(0.8, abs=2e-6)


def test_cross_industry_engine_rejects_a_short_explicit_range(monkeypatch):
    days = _days(119)
    selected = ["telecom"]
    _install_history_loader(monkeypatch, _histories(selected, days))
    query = _query(selected, days, crisis_cash_buffer=0.1, holding_count=3)

    with pytest.raises(RuntimeError) as exc_info:
        cross.run_cross_industry_backtest(query)

    assert "119" in str(exc_info.value)
    assert str(cross.MIN_BACKTEST_TRADING_DAYS) in str(exc_info.value)


def test_cross_industry_engine_rejects_a_selected_industry_with_no_data(monkeypatch):
    days = _days(130)
    selected = ["telecom", "hydro"]
    histories = _histories(["telecom"], days)
    _install_history_loader(monkeypatch, histories)
    query = _query(
        selected,
        days,
        max_industry_weight=0.45,
        crisis_cash_buffer=0.1,
        holding_count=6,
    )

    with pytest.raises(RuntimeError) as exc_info:
        cross.run_cross_industry_backtest(query)

    assert "hydro" in str(exc_info.value) or "\u6c34\u7535" in str(exc_info.value)


def test_strategy_cache_uses_requested_boundaries_not_file_age(monkeypatch, tmp_path):
    monkeypatch.setattr(history_source, "_CACHE_DIR", tmp_path)
    points = {
        date(2015, 1, 5): SecurityMarketPoint(close=10.0, pb=1.0, pe=10.0),
        date(2015, 1, 6): SecurityMarketPoint(close=10.1, pb=1.0, pe=10.0),
    }
    history_source._write_cache(
        "sh.600519",
        "raw",
        points,
        requested_start=date(2010, 1, 1),
        requested_end=date(2015, 1, 6),
    )

    assert history_source._cache_covers(
        "sh.600519", "raw", points, date(2010, 1, 1), date(2015, 1, 6)
    )
    assert not history_source._cache_covers(
        "sh.600519", "raw", points, date(2010, 1, 1), date(2026, 1, 1)
    )


def test_strategy_cache_remembers_ipo_prelisting_request_range(monkeypatch, tmp_path):
    monkeypatch.setattr(history_source, "_CACHE_DIR", tmp_path)
    points = {
        date(2022, 1, 5): SecurityMarketPoint(close=50.0, pb=1.0, pe=10.0),
        date(2026, 1, 5): SecurityMarketPoint(close=80.0, pb=1.2, pe=12.0),
    }
    history_source._write_cache(
        "sh.600941",
        "post",
        points,
        requested_start=date(2020, 1, 1),
        requested_end=date(2026, 1, 5),
    )

    assert history_source._cache_covers(
        "sh.600941", "post", points, date(2020, 1, 1), date(2026, 1, 5)
    )


def test_crisis_cash_buffer_reduces_a_common_price_shock(monkeypatch):
    days = _days(180)
    histories = _histories(["telecom"], days)
    for stock_history in histories.values():
        last = stock_history[days[-1]]
        stock_history[days[-1]] = SecurityMarketPoint(
            close=last.close * .5,
            pb=last.pb,
            pe=last.pe,
        )
    _install_history_loader(monkeypatch, histories)

    with_buffer = cross.run_cross_industry_backtest(
        _query(["telecom"], days, crisis_cash_buffer=.2, holding_count=3)
    )
    without_buffer = cross.run_cross_industry_backtest(
        _query(["telecom"], days, crisis_cash_buffer=0, holding_count=3)
    )

    assert with_buffer.results[0].equity_curve[-1].value == pytest.approx(60.0)
    assert without_buffer.results[0].equity_curve[-1].value == pytest.approx(50.0)
