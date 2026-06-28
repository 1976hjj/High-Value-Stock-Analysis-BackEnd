"""Dividend-yield floor valuation."""
from __future__ import annotations


def dividend_floor_price(dividend_per_share: float, target_dividend_yield: float) -> float | None:
    if dividend_per_share <= 0 or target_dividend_yield <= 0:
        return None
    return dividend_per_share / target_dividend_yield


def dividend_stress_floor_price(dividend_per_share: float, cut_ratio: float, target_dividend_yield: float) -> float | None:
    if not 0 <= cut_ratio <= 1:
        raise ValueError("cut_ratio must be between 0 and 1")
    return dividend_floor_price(dividend_per_share * cut_ratio, target_dividend_yield)
