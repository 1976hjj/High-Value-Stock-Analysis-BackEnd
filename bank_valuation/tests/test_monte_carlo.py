from bank_valuation.app.valuation.monte_carlo import run_monte_carlo

def test_monte_carlo_returns_ordered_quantiles(bank):
    result = run_monte_carlo(bank, years=3, simulations=300, seed=7)
    assert result["p10_price"] <= result["p50_price"] <= result["p90_price"]
    assert 0 <= result["probability_price_down_20"] <= 1
