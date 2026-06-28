from bank_valuation.app.valuation.scenario import scenario_probabilities, scenario_prices

def test_scenario_probabilities_sum_to_one(bank):
    probabilities = scenario_probabilities(bank, .05)
    assert sum(probabilities.values()) == 1
    scenarios = scenario_prices(bank, probabilities)
    assert scenarios["bull"]["price_range"] == (8.1, 11.7)
