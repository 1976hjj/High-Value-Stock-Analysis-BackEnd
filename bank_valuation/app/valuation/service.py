"""Application service that composes individual valuation models."""
from __future__ import annotations
import logging
from ..models import BankInput, DefensiveDecision, StressTestItem, ValuationResponse
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
    dividend_floor = _round_or_none(dividend_floor_price(bank.dividend_per_share, .05))
    dividend_stress_floor = _round_or_none(dividend_stress_floor_price(bank.dividend_per_share, .7, .05))
    fair_price = round(bank.bps * fair_multiple, 4)
    upside_potential = round(base_high / bank.current_price - 1, 6)
    downside_risk = round(crisis_low / bank.current_price - 1, 6)
    margin_of_safety = round(1 - bank.current_price / max(base_low, 0.000001), 6)
    analysis = risk_analysis(bank, percentiles["pb_percentile_3y"], flags)
    rating = final_rating(bank, percentiles["pb_percentile_3y"], flags)
    response = ValuationResponse(
        stock_code=bank.stock_code, stock_name=bank.stock_name, bank_profile=bank_profile(bank.stock_code),
        current_price=round(bank.current_price, 4),
        daily_change_pct=round(bank.daily_change_pct, 6) if bank.daily_change_pct is not None else None,
        current_pb=round(bank.pb_current, 4),
        market_date=bank.market_date, financial_report_date=bank.financial_report_date,
        cost_of_equity=round(coe, 6), **percentiles,
        fair_pb_pb_roe=round(fair_multiple, 4), pb_roe_fair_price=fair_price,
        dividend_floor_price=dividend_floor,
        dividend_stress_floor_price=dividend_stress_floor,
        residual_income_price=residual_income_prices["5y"], residual_income_prices=residual_income_prices,
        input_snapshot=_snapshot(bank), pb_history_chart=_sample_pb_history(bank),
        scenarios=scenarios, scenario_probabilities=probabilities,
        upside_potential=upside_potential,
        downside_risk=downside_risk,
        margin_of_safety=margin_of_safety,
        risk_flags=flags,
        risk_analysis=analysis,
        defensive_decision=_defensive_decision(
            bank=bank,
            scenarios=scenarios,
            fair_price=fair_price,
            dividend_floor=dividend_floor,
            dividend_stress_floor=dividend_stress_floor,
            residual_income_price=residual_income_prices["5y"],
            risk_flags=flags,
            risk_analysis=analysis,
            final_rating_value=rating,
            downside_risk=downside_risk,
            pb_percentile_3y=percentiles["pb_percentile_3y"],
        ),
        final_rating=rating,
    )
    logger.info("Valuation complete: code=%s rating=%s fair_price=%.4f base_range=%s risk_flags=%d", response.stock_code, response.final_rating, response.pb_roe_fair_price, response.scenarios["base"]["price_range"], len(response.risk_flags))
    return response


def _round_or_none(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _defensive_decision(
    *,
    bank: BankInput,
    scenarios: dict[str, dict],
    fair_price: float,
    dividend_floor: float | None,
    dividend_stress_floor: float | None,
    residual_income_price: float,
    risk_flags: list[str],
    risk_analysis,
    final_rating_value: str,
    downside_risk: float,
    pb_percentile_3y: float,
) -> DefensiveDecision:
    red_driver_count = sum(1 for driver in risk_analysis.drivers if driver.status == "risk")
    watch_driver_count = sum(1 for driver in risk_analysis.drivers if driver.status == "watch")
    required_margin = .10
    if final_rating_value in {"watch", "hold_income"}:
        required_margin = .14
    if risk_flags or watch_driver_count >= 2:
        required_margin = max(required_margin, .18)
    if final_rating_value in {"value_trap_risk", "avoid"} or red_driver_count:
        required_margin = max(required_margin, .25)

    base_low = scenarios["base"]["price_range"][0]
    value_anchor = _positive_median([base_low, fair_price, residual_income_price])
    wait_price = value_anchor * (1 - required_margin)
    if dividend_floor is not None and bank.dividend_yield >= .04:
        wait_price = min(wait_price, dividend_floor * .98)
    if final_rating_value in {"value_trap_risk", "avoid"}:
        wait_price = min(wait_price, scenarios["bear"]["price_range"][0] * .9)
    buy_wait_gap = wait_price / bank.current_price - 1
    if final_rating_value == "avoid":
        buy_wait_status = "avoid"
        buy_wait_reason = "红灯风险较多，等待价仅作风险锚点，优先等待基本面改善。"
    elif buy_wait_gap >= .03:
        buy_wait_status = "reached"
        buy_wait_reason = "当前价已低于保守等待价，后续重点看红绿灯是否仍可接受。"
    elif buy_wait_gap >= -.04:
        buy_wait_status = "near"
        buy_wait_reason = "当前价接近等待区，适合继续观察成交价与风险信号。"
    else:
        buy_wait_status = "wait"
        buy_wait_reason = "当前价距离保守等待价仍有空间，等待更高安全边际。"

    dividend_stress_gap = None if dividend_stress_floor is None else dividend_stress_floor / bank.current_price - 1
    dividend_stress_note = (
        "按分红削减 30% 后仍要求约 5% 股息率反推；高于现价时表示削减后股息托底仍较强，不是预测涨幅。"
        if dividend_stress_gap is not None and dividend_stress_gap > 0
        else "按分红削减 30% 后仍要求约 5% 股息率反推；低于现价时表示分红托底在压力下会下移。"
    )
    stress_tests = [
        _stress_item(
            "分红压力托底",
            dividend_stress_floor,
            bank.current_price,
            dividend_stress_note,
        ),
        _stress_item(
            "悲观情景下沿",
            scenarios["bear"]["price_range"][0],
            bank.current_price,
            "利润和估值同时承压时的价格下沿。",
        ),
        _stress_item(
            "危机情景下沿",
            scenarios["crisis"]["price_range"][0],
            bank.current_price,
            "极端风险偏好下修时的底线压力位。",
        ),
    ]
    red_stress_count = sum(1 for item in stress_tests if item.severity == "red")
    yellow_stress_count = sum(1 for item in stress_tests if item.severity == "yellow")
    risk_reasons: list[str] = []
    if final_rating_value in {"value_trap_risk", "avoid"}:
        risk_reasons.append("综合评级已进入风险折价区")
    if risk_flags:
        risk_reasons.extend(risk_flags[:2])
    if red_driver_count:
        risk_reasons.append("存在红色经营风险驱动项")
    if downside_risk < -.35:
        risk_reasons.append("危机情景下行空间较大")
    if pb_percentile_3y > .75:
        risk_reasons.append("PB估值分位偏高")
    if bank.profit_growth_yoy < 0:
        risk_reasons.append("利润同比负增长")
    if not risk_reasons:
        risk_reasons.append("资产质量、资本和盈利未触发硬风险")

    if final_rating_value in {"value_trap_risk", "avoid"} or red_driver_count or len(risk_flags) >= 2:
        light = "red"
        label = "红灯：先排除风险"
    elif risk_flags or watch_driver_count or yellow_stress_count >= 2 or downside_risk < -.25:
        light = "yellow"
        label = "黄灯：可观察但要留安全边际"
    else:
        light = "green"
        label = "绿灯：风险信号相对干净"

    return DefensiveDecision(
        buy_wait_price=round(wait_price, 4),
        buy_wait_gap=round(buy_wait_gap, 6),
        buy_wait_status=buy_wait_status,
        buy_wait_reason=buy_wait_reason,
        risk_light=light,
        risk_light_label=label,
        risk_light_reasons=risk_reasons[:4],
        stress_tests=stress_tests,
    )


def _stress_item(name: str, stressed_price: float | None, current_price: float, note: str) -> StressTestItem:
    downside = None if stressed_price is None else stressed_price / current_price - 1
    if downside is None:
        severity = "yellow"
    elif downside >= -.15:
        severity = "green"
    elif downside >= -.32:
        severity = "yellow"
    else:
        severity = "red"
    return StressTestItem(
        name=name,
        stressed_price=round(stressed_price, 4) if stressed_price is not None else None,
        downside=round(downside, 6) if downside is not None else None,
        severity=severity,
        note=note,
    )


def _positive_median(values: list[float | None]) -> float:
    valid = sorted(value for value in values if value is not None and value > 0)
    if not valid:
        return 0.0
    middle = len(valid) // 2
    if len(valid) % 2:
        return valid[middle]
    return (valid[middle - 1] + valid[middle]) / 2


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
    """Return chart-ready PB/PE and actual close-price history without a daily payload."""
    if not bank.pb_history_dates or len(bank.pb_history_dates) != len(bank.pb_history):
        return []
    step = max(1, len(bank.pb_history) // max_points)
    prices_match = len(bank.price_history) == len(bank.pb_history)
    pe_match = len(bank.pe_history) == len(bank.pb_history)
    points = [{
        "date": str(bank.pb_history_dates[index]), "pb": round(bank.pb_history[index], 4),
        "pe": round(bank.pe_history[index], 4) if pe_match and bank.pe_history[index] is not None else None,
        "close": round(bank.price_history[index], 4) if prices_match else None,
    } for index in range(0, len(bank.pb_history), step)]
    if points[-1]["date"] != str(bank.pb_history_dates[-1]):
        points.append({
            "date": str(bank.pb_history_dates[-1]), "pb": round(bank.pb_history[-1], 4),
            "pe": round(bank.pe_history[-1], 4) if pe_match and bank.pe_history[-1] is not None else None,
            "close": round(bank.price_history[-1], 4) if prices_match else None,
        })
    return points
