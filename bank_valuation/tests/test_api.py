from fastapi.testclient import TestClient
from bank_valuation.app.main import app


def test_scenario_template_endpoint():
    response = TestClient(app).get("/api/bank/scenario-template")
    assert response.status_code == 200
    assert set(response.json()) == {"bull", "base", "bear", "crisis"}
