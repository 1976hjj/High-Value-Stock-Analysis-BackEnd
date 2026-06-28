import pytest
from bank_valuation.app.valuation.pb_roe import fair_pb

def test_pb_roe_formula():
    assert fair_pb(.09, .03, .08) == pytest.approx(1.2)

def test_growth_must_be_lower_than_cost_of_equity():
    with pytest.raises(ValueError): fair_pb(.09, .08, .08)
