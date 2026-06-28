import pytest
from bank_valuation.app.valuation.dividend import dividend_floor_price, dividend_stress_floor_price

def test_dividend_floors():
    assert dividend_floor_price(.30, .05) == pytest.approx(6)
    assert dividend_stress_floor_price(.30, .7, .05) == pytest.approx(4.2)
    assert dividend_floor_price(0, .05) is None
