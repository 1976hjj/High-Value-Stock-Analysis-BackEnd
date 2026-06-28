"""Application service that composes individual valuation models."""
from __future__ import annotations
import logging
from ..models import BankInput, ValuationResponse
from .pb_roe import cost_of_equity, fair_pb
from .dividend import dividend_floor_price, dividend_stress_floor_price
from .historical_pb import historical_pb_percentiles
from .residual_income import intrinsic_value
from .scenario import scenario_probabilities, scenario_prices
from .scoring import risk_flags, final_rating, risk_analysis
from ..data_sources.bank_profiles import bank_profile

logger = logging.getLogger("bank_valuation.valuation")


def value_bank(bank: BankInput) -> ValuationResponse:
    logger.info("Start valuation: code=%s price=%.4f pb=%.4f roe=%.4f", bank.stock_code, bank.current_price, bank.pb_current, bank.roe)
    coe = cost_of_equity(bank.risk_free_rate, bank.beta, bank.equity_risk_premium)
    fair_multiple = fair_pb(bank.roe, bank.long_term_growth, coe)
    percentiles = historical_pb_percentiles(bank.pb_current, bank.pb_history)
    probabilities = scenario_probabilities(bank, percentiles["pb_percentile_3y"])
    scenarios = scenario_prices(bank, probabilities)
    base_low, base_high = scenarios["base"]["price_range"]
    crisis_low = scenarios["crisis"]["price_range"][0]
    flags = risk_flags(bank, percentiles["pb_percentile_3y"])
    residual_income_prices = {
        f"{years}y": round(intrinsic_value(bank.bps, bank.roe, coe, bank.payout_ratio, bank.long_term_growth, years), 4)
        for years in (3, 5, 10)
    }
    response = ValuationResponse(
        stock_code=bank.stock_code, stock_name=bank.stock_name, bank_profile=bank_profile(bank.stock_code),
        current_price=round(bank.current_price, 4),
        daily_change_pct=round(bank.daily_change_pct, 6) if bank.daily_change_pct is not None else None,
        current_pb=round(bank.pb_current, 4),
        market_date=bank.market_date, financial_report_date=bank.financial_report_date,
        cost_of_equity=round(coe, 6), **percentiles,
        fair_pb_pb_roe=round(fair_multiple, 4), pb_roe_fair_price=round(bank.bps * fair_multiple, 4),
        dividend_floor_price=_round_or_none(dividend_floor_price(bank.dividend_per_share, .05)),
        dividend_stress_floor_price=_round_or_none(dividend_stress_floor_price(bank.dividend_per_share, .7, .05)),
        residual_income_price=residual_income_prices["5y"], residual_income_prices=residual_income_prices,
        input_snapshot=_snapshot(bank), pb_history_chart=_sample_pb_history(bank),
        scenarios=scenarios, scenario_probabilities=probabilities,
        upside_potential=round(base_high / bank.current_price - 1, 6),
        downside_risk=round(crisis_low / bank.current_price - 1, 6),
        margin_of_safety=round(1 - bank.current_price / max(base_low, 0.000001), 6),
        risk_flags=flags, risk_analysis=risk_analysis(bank, percentiles["pb_percentile_3y"], flags),
        final_rating=final_rating(bank, percentiles["pb_percentile_3y"], flags),
    )
    logger.info("Valuation complete: code=%s rating=%s fair_price=%.4f base_range=%s risk_flags=%d", response.stock_code, response.final_rating, response.pb_roe_fair_price, response.scenarios["base"]["price_range"], len(response.risk_flags))
    return response


def _round_or_none(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _snapshot(bank: BankInput) -> dict[str, float | str | bool | None]:
    """Auditable inputs used for the current valuation run."""
    return {
        "daily_change_pct": bank.daily_change_pct, "bps": bank.bps, "eps": bank.eps, "roe": bank.roe, "net_profit": bank.net_profit,
        "profit_growth_yoy": bank.profit_growth_yoy, "dividend_per_share": bank.dividend_per_share,
        "payout_ratio": bank.payout_ratio, "dividend_yield": bank.dividend_yield,
        "pe_current": bank.pe_current, "nim": bank.nim, "npl_ratio": bank.npl_ratio,
        "provision_coverage": bank.provision_coverage, "provision_coverage_report_date": str(bank.provision_coverage_report_date) if bank.provision_coverage_report_date else None, "provision_coverage_source": bank.provision_coverage_source, "bank_special_metrics_report_date": str(bank.bank_special_metrics_report_date) if bank.bank_special_metrics_report_date else None, "bank_special_metrics_source": bank.bank_special_metrics_source, "cet1_ratio": bank.cet1_ratio, "net_interest_spread": bank.net_interest_spread, "loan_provision_ratio": bank.loan_provision_ratio, "loan_to_deposit_ratio": bank.loan_to_deposit_ratio,
        "capital_adequacy_ratio": bank.capital_adequacy_ratio, "risk_free_rate": bank.risk_free_rate,
        "equity_risk_premium": bank.equity_risk_premium, "beta": bank.beta,
        "long_term_growth": bank.long_term_growth, "roe_trend": bank.roe_trend,
        "nim_change": bank.nim_change, "npl_ratio_change": bank.npl_ratio_change,
        "provision_coverage_change": bank.provision_coverage_change, "dividend_stable": bank.dividend_stable,
    }


def _sample_pb_history(bank: BankInput, max_points: int = 120) -> list[dict[str, float | str | None]]:
    """Return chart-ready PB and actual close-price history without a daily payload."""
    if not bank.pb_history_dates or len(bank.pb_history_dates) != len(bank.pb_history):
        return []
    step = max(1, len(bank.pb_history) // max_points)
    prices_match = len(bank.price_history) == len(bank.pb_history)
    points = [{
        "date": str(bank.pb_history_dates[index]), "pb": round(bank.pb_history[index], 4),
        "close": round(bank.price_history[index], 4) if prices_match else None,
    } for index in range(0, len(bank.pb_history), step)]
    if points[-1]["date"] != str(bank.pb_history_dates[-1]):
        points.append({
            "date": str(bank.pb_history_dates[-1]), "pb": round(bank.pb_history[-1], 4),
            "close": round(bank.price_history[-1], 4) if prices_match else None,
        })
    return points
