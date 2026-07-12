from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from bank_valuation.app.main import app
from bank_valuation.app.models import (
    IndustryPanoramaMetric,
    IndustryRankingQuery,
)
from bank_valuation.app.valuation import industry_ranking


def _metric(key: str, score: float) -> IndustryPanoramaMetric:
    return IndustryPanoramaMetric(
        key=key,
        label=key,
        value=f"{score:.1f}",
        raw_value=score / 100,
        score=score,
        status="strong" if score >= 82 else "stable",
        quality="reported",
        interpretation="test",
        source="test fixture",
    )


def _analysis(query, score: float):
    group = lambda group_id, keys: SimpleNamespace(id=group_id, metrics=[_metric(key, score) for key in keys])
    if query.industry_id == "resources":
        groups = [
            group("cycle", ["commodity_revenue_proxy", "commodity_profit_proxy"]),
            group("cost", ["gross_margin"]),
            group("balance", ["cfo_to_np", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    elif query.industry_id == "oilgas":
        groups = [
            group("upstream", ["oil_revenue_proxy", "oil_profit_proxy"]),
            group("integration", ["inventory_days", "asset_turnover"]),
            group("capital", ["cfo_to_np", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    elif query.industry_id == "tollroad":
        groups = [
            group("traffic", ["traffic_revenue_proxy", "traffic_profit_proxy"]),
            group("asset", ["asset_turnover", "cfo_to_revenue"]),
            group("finance", ["cfo_to_np", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    elif query.industry_id == "nuclear":
        groups = [
            group("generation", ["nuclear_generation_proxy", "nuclear_profit_proxy"]),
            group("construction", ["asset_growth", "asset_turnover"]),
            group("finance", ["cfo_to_np", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    elif query.industry_id == "telecom":
        groups = [
            group("subscriber", ["telecom_revenue_proxy", "telecom_profit_proxy"]),
            group("network", ["asset_growth", "asset_turnover"]),
            group("finance", ["cfo_to_np", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    else:
        groups = [
            group("asset", ["profit_growth_proxy", "revenue_growth_proxy"]),
            group("operation", ["gross_margin"]),
            group("finance", ["cfo_to_np", "interest_cover", "liability_ratio"]),
            group("return", ["dividend_yield", "valuation_percentile"]),
        ]
    return SimpleNamespace(
        stock_code=query.stock_code,
        stock_name=query.stock_code,
        market_date=date(2026, 7, 10),
        panorama=SimpleNamespace(report_date=date(2026, 3, 31), groups=groups),
        scores=SimpleNamespace(valuation=75.0),
        valuation_percentile=.25,
        risk_flags=[],
    )


def test_ranking_query_only_accepts_automated_industries():
    with pytest.raises(ValidationError):
        IndustryRankingQuery(industry_id="bank")


def test_hydro_ranking_is_sorted_by_data_driven_scores(monkeypatch):
    scores = {"600900": 92.0, "600025": 80.0, "600674": 76.0}

    def fake_analysis(query):
        return _analysis(query, scores.get(query.stock_code, 68.0))

    monkeypatch.setattr(industry_ranking, "analyze_industry_stock", fake_analysis)
    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="hydro", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 6
    assert result.results[0].stock_code == "600900"
    assert [row.rank for row in result.results] == list(range(1, 7))
    assert result.results[0].key_metrics
    assert result.failures == []
    assert "不使用回测结果" in result.data_note


def test_resource_ranking_uses_resource_cycle_groups(monkeypatch):
    scores = {"601088": 86.0, "601899": 79.0}
    monkeypatch.setattr(
        industry_ranking,
        "analyze_industry_stock",
        lambda query: _analysis(query, scores.get(query.stock_code, 66.0)),
    )

    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="resources", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 6
    assert result.results[0].stock_code == "601088"
    assert result.results[0].key_metrics[0].key == "commodity_revenue_proxy"
    assert result.failures == []


def test_oilgas_ranking_uses_integrated_energy_groups(monkeypatch):
    scores = {"600938": 88.0, "601857": 80.0, "600028": 74.0}
    monkeypatch.setattr(
        industry_ranking,
        "analyze_industry_stock",
        lambda query: _analysis(query, scores.get(query.stock_code, 65.0)),
    )

    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="oilgas", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 3
    assert result.results[0].stock_code == "600938"
    assert result.results[0].key_metrics[0].key == "oil_revenue_proxy"
    assert result.failures == []


def test_tollroad_ranking_uses_traffic_and_finance_groups(monkeypatch):
    scores = {"600377": 88.0, "600350": 82.0, "001965": 78.0}
    monkeypatch.setattr(
        industry_ranking,
        "analyze_industry_stock",
        lambda query: _analysis(query, scores.get(query.stock_code, 70.0)),
    )

    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="tollroad", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 5
    assert result.results[0].stock_code == "600377"
    assert result.results[0].key_metrics[0].key == "traffic_revenue_proxy"
    assert result.failures == []


def test_nuclear_ranking_uses_generation_and_construction_groups(monkeypatch):
    scores = {"601985": 86.0, "003816": 80.0}
    monkeypatch.setattr(
        industry_ranking,
        "analyze_industry_stock",
        lambda query: _analysis(query, scores.get(query.stock_code, 68.0)),
    )

    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="nuclear", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 2
    assert result.results[0].stock_code == "601985"
    assert result.results[0].key_metrics[0].key == "nuclear_generation_proxy"
    assert result.failures == []


def test_telecom_ranking_uses_subscriber_and_network_groups(monkeypatch):
    scores = {"600941": 90.0, "601728": 83.0, "600050": 76.0}
    monkeypatch.setattr(
        industry_ranking,
        "analyze_industry_stock",
        lambda query: _analysis(query, scores.get(query.stock_code, 68.0)),
    )

    result = industry_ranking.run_industry_ranking(
        IndustryRankingQuery(industry_id="telecom", valuation_date=date(2026, 7, 11))
    )

    assert result.result_count == 3
    assert result.results[0].stock_code == "600941"
    assert result.results[0].key_metrics[0].key == "telecom_revenue_proxy"
    assert result.failures == []


def test_industry_ranking_api_contract(monkeypatch):
    def fake_ranking(query):
        return {
            "module": "industry_ranking",
            "industry_id": query.industry_id,
            "valuation_date": query.valuation_date or date(2026, 7, 11),
            "result_count": 0,
            "results": [],
            "failures": [],
            "data_note": "test",
        }

    monkeypatch.setattr("bank_valuation.app.routers.strategy.run_industry_ranking", fake_ranking)
    response = TestClient(app).post(
        "/api/industry/ranking",
        json={"industry_id": "consumer", "valuation_date": "2026-07-11"},
    )

    assert response.status_code == 200
    assert response.json()["module"] == "industry_ranking"
    assert response.json()["industry_id"] == "consumer"
