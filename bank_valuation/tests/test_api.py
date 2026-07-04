from fastapi.testclient import TestClient
from bank_valuation.app.main import app


def test_scenario_template_endpoint():
    response = TestClient(app).get("/api/bank/scenario-template")
    assert response.status_code == 200
    assert set(response.json()) == {"bull", "base", "bear", "crisis"}


def test_mean_reversion_overview_endpoint(monkeypatch):
    def fake_overview(valuation_date, refresh_cache, include_risky):
        return {
            "module": "bank_mean_reversion_overview",
            "title": "银行低估与均值回归排序",
            "as_of_date": "2025-06-10",
            "count": 0,
            "investable_count": 0,
            "risky_count": 0,
            "failed_count": 0,
            "results": [],
            "failures": [],
            "data_note": "test",
        }

    monkeypatch.setattr("bank_valuation.app.routers.bank.bank_mean_reversion_overview", fake_overview)
    response = TestClient(app).post("/api/bank/mean-reversion-overview", json={"valuation_date": "2025-06-10"})
    assert response.status_code == 200
    assert response.json()["module"] == "bank_mean_reversion_overview"
