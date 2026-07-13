from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from bank_valuation.app.data_sources import financial_snapshot as financial
from bank_valuation.app.data_sources.financial_snapshot import FinancialSnapshot
from bank_valuation.app.valuation.industry_panorama import build_industry_panorama
from bank_valuation.app.valuation.industry_price_projection import build_price_projection


@pytest.fixture
def snapshot() -> FinancialSnapshot:
    return FinancialSnapshot(
        code="sh.600519",
        report_date=date(2026, 3, 31),
        published_date=date(2026, 4, 25),
        fiscal_year=2026,
        fiscal_quarter=1,
        cash_dividend_ttm=51.98123,
        values={
            "revenue": 53_909_240_854.0,
            "revenue_yoy": .065,
            "YOYNI": .014,
            "gpMargin": .898,
            "npMargin": .522,
            "YOYAsset": .024,
            "INVTurnDays": 995.6,
            "NRTurnDays": .03,
            "INVTurnRatio": .09,
            "NRTurnRatio": 3084.6,
            "CATurnRatio": .209,
            "CFOToOR": .499,
            "roe_annualized": .423,
            "CFOToNP": .956,
            "AssetTurnRatio": .175,
            "liabilityToAsset": .121,
            "quickRatio": 5.48,
            "epsTTM": 66.05,
            "cfo_annualized": 107_639_530_397.0,
            "totalShare": 1_252_270_215.0,
        },
    )


def test_consumer_panorama_contains_real_values_and_metric_provenance(snapshot):
    panorama = build_industry_panorama(
        "consumer",
        snapshot,
        current_price=1210,
        current_pe=18.3,
        current_pb=8.2,
        valuation_percentile=.31,
        volatility=.18,
        drawdown=-.12,
    )

    assert panorama is not None
    assert panorama.report_date == date(2026, 3, 31)
    assert len(panorama.groups) == 4
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["revenue"].value == "539.09 亿元"
    assert metrics["gross_margin"].value == "89.8%"
    assert metrics["dividend_yield"].quality == "derived"
    assert metrics["revenue"].raw_value == pytest.approx(53_909_240_854.0)


def test_financial_snapshot_dividend_uses_latest_fiscal_year(monkeypatch):
    rows = {
        "dividend 2026": [
            {"statYear": "2025", "dividOperateDate": "2026-01-16", "dividCashPsBeforeTax": "1.013"},
            {"statYear": "2025", "dividOperateDate": "2026-07-10", "dividCashPsBeforeTax": "1.003"},
        ],
        "dividend 2025": [
            {"statYear": "2024", "dividOperateDate": "2025-07-11", "dividCashPsBeforeTax": "2.0"},
        ],
        "dividend 2024": [],
    }
    calls: list[int] = []

    def query_dividend_data(*args, **kwargs):
        calls.append(kwargs["year"])
        return object()

    monkeypatch.setattr(financial, "bs", SimpleNamespace(query_dividend_data=query_dividend_data))
    monkeypatch.setattr(financial, "_rows", lambda query: rows[f"dividend {calls[-1]}"])

    # 2024's annual payout is nearby in calendar time but belongs to the
    # previous fiscal year, so it cannot turn 2.016 into 4.016.
    assert financial._query_dividend_ttm("sh.600036", date(2026, 7, 10)) == pytest.approx(2.016)


def test_hydro_panorama_marks_water_related_financial_signal_as_proxy(snapshot):
    panorama = build_industry_panorama(
        "hydro",
        snapshot,
        current_price=28,
        current_pe=19,
        current_pb=3,
        valuation_percentile=.23,
        volatility=.12,
        drawdown=-.06,
    )

    assert panorama is not None
    first = panorama.groups[0].metrics[0]
    assert first.key == "profit_growth_proxy"
    assert first.quality == "proxy"
    assert "复核" in first.interpretation


def test_resources_panorama_exposes_cycle_cost_balance_and_return_groups(snapshot):
    panorama = build_industry_panorama(
        "resources",
        replace(snapshot, cash_dividend_ttm=2.26),
        current_price=42.0,
        current_pe=10.0,
        current_pb=1.8,
        valuation_percentile=.38,
        volatility=.28,
        drawdown=-.18,
    )

    assert panorama is not None
    assert [group.id for group in panorama.groups] == ["cycle", "cost", "balance", "return"]
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["commodity_revenue_proxy"].quality == "proxy"
    assert metrics["commodity_profit_proxy"].quality == "proxy"
    assert metrics["gross_margin"].quality == "reported"
    assert metrics["dividend_yield"].raw_value == pytest.approx(2.26 / 42.0)


def test_oilgas_panorama_exposes_upstream_integration_capital_and_return_groups(snapshot):
    panorama = build_industry_panorama(
        "oilgas",
        replace(snapshot, cash_dividend_ttm=1.25),
        current_price=28.0,
        current_pe=9.5,
        current_pb=1.4,
        valuation_percentile=.42,
        volatility=.25,
        drawdown=-.16,
    )

    assert panorama is not None
    assert [group.id for group in panorama.groups] == ["upstream", "integration", "capital", "return"]
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["oil_revenue_proxy"].quality == "proxy"
    assert metrics["oil_profit_proxy"].quality == "proxy"
    assert metrics["inventory_days"].quality == "reported"
    assert metrics["dividend_yield"].raw_value == pytest.approx(1.25 / 28.0)


def test_tollroad_panorama_exposes_traffic_asset_finance_and_return_groups(snapshot):
    panorama = build_industry_panorama(
        "tollroad",
        replace(snapshot, cash_dividend_ttm=.58),
        current_price=13.0,
        current_pe=12.0,
        current_pb=1.7,
        valuation_percentile=.36,
        volatility=.16,
        drawdown=-.10,
    )

    assert panorama is not None
    assert [group.id for group in panorama.groups] == ["traffic", "asset", "finance", "return"]
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["traffic_revenue_proxy"].quality == "proxy"
    assert metrics["traffic_profit_proxy"].quality == "proxy"
    assert metrics["cfo_to_revenue"].quality == "reported"
    assert metrics["dividend_yield"].raw_value == pytest.approx(.58 / 13.0)


def test_nuclear_panorama_exposes_generation_construction_finance_and_return_groups(snapshot):
    panorama = build_industry_panorama(
        "nuclear",
        replace(snapshot, cash_dividend_ttm=.22),
        current_price=10.0,
        current_pe=18.0,
        current_pb=1.8,
        valuation_percentile=.44,
        volatility=.20,
        drawdown=-.13,
    )

    assert panorama is not None
    assert [group.id for group in panorama.groups] == ["generation", "construction", "finance", "return"]
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["nuclear_generation_proxy"].quality == "proxy"
    assert metrics["nuclear_profit_proxy"].quality == "proxy"
    assert metrics["asset_growth"].quality == "proxy"
    assert metrics["dividend_yield"].raw_value == pytest.approx(.22 / 10.0)


def test_telecom_panorama_exposes_subscriber_network_finance_and_return_groups(snapshot):
    panorama = build_industry_panorama(
        "telecom",
        replace(snapshot, cash_dividend_ttm=5.1),
        current_price=110.0,
        current_pe=16.0,
        current_pb=1.8,
        valuation_percentile=.40,
        volatility=.17,
        drawdown=-.09,
    )

    assert panorama is not None
    assert [group.id for group in panorama.groups] == ["subscriber", "network", "finance", "return"]
    metrics = {metric.key: metric for group in panorama.groups for metric in group.metrics}
    assert metrics["telecom_revenue_proxy"].quality == "proxy"
    assert metrics["telecom_profit_proxy"].quality == "proxy"
    assert metrics["asset_growth"].quality == "proxy"
    assert metrics["dividend_yield"].raw_value == pytest.approx(5.1 / 110.0)


def test_financial_snapshot_cache_round_trip_preserves_point_in_time_dates(tmp_path, monkeypatch, snapshot):
    monkeypatch.setattr(financial, "_CACHE_DIR", tmp_path)
    valuation_date = date(2026, 7, 11)

    financial._write_cache(snapshot, valuation_date)
    loaded = financial._read_cache(snapshot.code, valuation_date)

    assert loaded == snapshot


def test_latest_published_excludes_reports_not_available_on_valuation_date():
    rows = [
        {"pubDate": "2026-04-25", "statDate": "2026-03-31", "netProfit": "1"},
        {"pubDate": "2026-08-30", "statDate": "2026-06-30", "netProfit": "2"},
    ]

    selected = financial._latest_published(rows, date(2026, 7, 11))

    assert selected is not None
    assert selected["statDate"] == "2026-03-31"


@pytest.mark.parametrize(
    ("industry_id", "current_price", "current_pe"),
    (("hydro", 28.03, 19.0074), ("consumer", 1204.98, 18.211), ("resources", 42.0, 10.0), ("oilgas", 28.0, 9.5), ("tollroad", 13.0, 12.0), ("nuclear", 10.0, 18.0), ("telecom", 110.0, 16.0)),
)
def test_industry_price_projection_is_calculated_from_panorama_data(
    snapshot, industry_id, current_price, current_pe
):
    if industry_id == "hydro":
        # Use a hydro-like per-share payout rather than the consumer fixture's payout.
        snapshot = replace(snapshot, cash_dividend_ttm=1.0)
    elif industry_id == "resources":
        snapshot = replace(snapshot, cash_dividend_ttm=2.26)
    elif industry_id == "oilgas":
        snapshot = replace(snapshot, cash_dividend_ttm=1.25)
    elif industry_id == "tollroad":
        snapshot = replace(snapshot, cash_dividend_ttm=.58)
    elif industry_id == "nuclear":
        snapshot = replace(snapshot, cash_dividend_ttm=.22)
    elif industry_id == "telecom":
        snapshot = replace(snapshot, cash_dividend_ttm=5.1)
    panorama = build_industry_panorama(
        industry_id,
        snapshot,
        current_price=current_price,
        current_pe=current_pe,
        current_pb=3.0,
        valuation_percentile=.31,
        volatility=.18,
        drawdown=-.12,
    )

    projection = build_price_projection(
        industry_id,
        panorama,
        current_price=current_price,
        current_pe=current_pe,
        market_date=date(2026, 7, 11),
        quality_score=78.0,
        valuation_percentile=.31,
    )

    assert projection is not None
    assert projection.implied_eps_ttm == pytest.approx(current_price / current_pe, rel=1e-3)
    assert [scenario.id for scenario in projection.scenarios] == ["bull", "base", "bear", "crisis"]
    for scenario in projection.scenarios:
        assert 0 < scenario.price_low < scenario.price_mid < scenario.price_high
        # Display prices are rounded to cents while returns retain more precision.
        assert scenario.return_mid == pytest.approx(scenario.price_mid / current_price - 1, abs=1e-3)
        assert scenario.drivers
        assert scenario.triggers
        assert scenario.formula
    by_id = {scenario.id: scenario for scenario in projection.scenarios}
    assert by_id["bull"].price_mid > by_id["base"].price_mid > by_id["bear"].price_mid
    assert projection.defensive_entry_price > 0
