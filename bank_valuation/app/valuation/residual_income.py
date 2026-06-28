"""Finite-horizon residual income model with terminal value."""
from __future__ import annotations


def intrinsic_value(
    current_book_value: float, roe: float, cost_of_equity: float, payout_ratio: float,
    long_term_growth: float, years: int = 5,
) -> float:
    if current_book_value <= 0:
        raise ValueError("current_book_value must be positive")
    if years not in (3, 5, 10):
        raise ValueError("years must be 3, 5, or 10")
    if cost_of_equity <= long_term_growth:
        raise ValueError("cost_of_equity must be greater than long_term_growth")
    if not 0 <= payout_ratio <= 1.5:
        raise ValueError("payout_ratio is outside supported range")

    book_value = current_book_value
    pv_residual_income = 0.0
    for year in range(1, years + 1):
        # A linear mean-reversion keeps the projection from extrapolating ROE forever.
        projected_roe = roe + (long_term_growth - roe) * (year - 1) / max(years, 1)
        residual_income = (projected_roe - cost_of_equity) * book_value
        pv_residual_income += residual_income / (1 + cost_of_equity) ** year
        book_value *= 1 + projected_roe * (1 - payout_ratio)

    terminal_roe = long_term_growth
    terminal_residual_income = (terminal_roe - cost_of_equity) * book_value
    terminal_value = terminal_residual_income / (cost_of_equity - long_term_growth)
    return max(0.0, current_book_value + pv_residual_income + terminal_value / (1 + cost_of_equity) ** years)
