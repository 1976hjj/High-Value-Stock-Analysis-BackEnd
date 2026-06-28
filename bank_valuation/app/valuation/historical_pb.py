"""Historical PB percentile helpers (252 trading days per year)."""
from __future__ import annotations


def percentile_rank(current_pb: float, history: list[float], years: int) -> float:
    if current_pb <= 0 or not history:
        raise ValueError("current_pb and PB history must be positive")
    window = history[-(years * 252):]
    return sum(value <= current_pb for value in window) / len(window)


def historical_pb_percentiles(current_pb: float, history: list[float]) -> dict[str, float]:
    return {f"pb_percentile_{years}y": percentile_rank(current_pb, history, years) for years in (3, 5, 10)}
