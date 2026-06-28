"""Dependency-free Monte Carlo price distribution."""
from __future__ import annotations
import random
from ..models import BankInput
from .scenario import scenario_probabilities
from .historical_pb import percentile_rank


def _weighted_scenario(rng: random.Random, probabilities: dict[str, float]) -> str:
    point = rng.random(); total = 0.0
    for name, weight in probabilities.items():
        total += weight
        if point <= total:
            return name
    return "crisis"


def run_monte_carlo(bank: BankInput, years: int = 5, simulations: int = 10_000, seed: int | None = 42) -> dict[str, float]:
    if years not in (3, 5):
        raise ValueError("Monte Carlo years must be 3 or 5")
    rng = random.Random(seed)
    probabilities = scenario_probabilities(bank, percentile_rank(bank.pb_current, bank.pb_history, 3))
    assumptions = {
        "bull": ((.11, .13), (.03, .06), (.90, 1.30)),
        "base": ((.08, .10), (.00, .03), (.60, .90)),
        "bear": ((.05, .07), (-.05, .00), (.35, .60)),
        "crisis": ((.00, .04), (-.15, -.05), (.15, .35)),
    }
    prices: list[float] = []
    for _ in range(simulations):
        name = _weighted_scenario(rng, probabilities)
        roe_range, growth_range, pb_range = assumptions[name]
        book_value = bank.bps
        for _ in range(years):
            roe = rng.uniform(*roe_range)
            growth = rng.uniform(*growth_range)
            payout = min(1.0, max(0.0, rng.gauss(bank.payout_ratio, 0.05)))
            # Growth adjusts profit retention modestly, while ROE drives book-value compounding.
            book_value *= max(0.5, 1 + roe * (1 - payout) + growth * 0.15)
        prices.append(book_value * rng.uniform(*pb_range))
    prices.sort()
    def q(value: float) -> float: return prices[min(len(prices) - 1, int(value * (len(prices) - 1)))]
    bear_floor = bank.bps * .35
    base_value = bank.bps * .75
    return {
        "probability_price_down_20": round(sum(p <= bank.current_price * .8 for p in prices) / simulations, 6),
        "probability_price_up_20": round(sum(p >= bank.current_price * 1.2 for p in prices) / simulations, 6),
        "probability_price_below_bear_floor": round(sum(p < bear_floor for p in prices) / simulations, 6),
        "probability_price_above_base_value": round(sum(p > base_value for p in prices) / simulations, 6),
        "expected_price": round(sum(prices) / simulations, 4),
        "p10_price": round(q(.10), 4), "p50_price": round(q(.50), 4), "p90_price": round(q(.90), 4),
    }
