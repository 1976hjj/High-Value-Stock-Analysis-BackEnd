"""PB-ROE (Gordon growth) valuation model."""
from __future__ import annotations


def cost_of_equity(risk_free_rate: float, beta: float, equity_risk_premium: float) -> float:
    return risk_free_rate + beta * equity_risk_premium


def fair_pb(roe: float, long_term_growth: float, cost_of_equity_value: float) -> float:
    """Return fair PB; a non-positive result is floored at zero.

    A perpetuity cannot be valued when required return is at or below growth.
    """
    if cost_of_equity_value <= long_term_growth:
        raise ValueError("cost_of_equity must be greater than long_term_growth")
    return max(0.0, (roe - long_term_growth) / (cost_of_equity_value - long_term_growth))
