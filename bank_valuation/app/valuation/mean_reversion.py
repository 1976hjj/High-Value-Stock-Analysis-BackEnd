"""All-bank undervaluation and mean-reversion ranking."""
from __future__ import annotations

import csv
from datetime import date
import logging
import statistics

from ..data_sources.bank_base import BANK_NAMES, _read_disk_cache, _snapshot_path, load_bank_input
from ..data_sources.bank_profiles import bank_profile
from ..models import BankInput, BankMeanReversionOverviewResponse, BankMeanReversionRow
from .service import value_bank

logger = logging.getLogger("bank_valuation.mean_reversion")


def bank_mean_reversion_overview(
    valuation_date: date | None = None,
    refresh_cache: bool = False,
    include_risky: bool = True,
) -> BankMeanReversionOverviewResponse:
    """Rank A-share banks by cheap-but-not-broken mean-reversion appeal."""
    rows: list[BankMeanReversionRow] = []
    failures: list[dict[str, str]] = []
    requested_date = valuation_date or date.today()
    for code in BANK_NAMES:
        try:
            bank = _load_overview_bank(code, valuation_date, requested_date, refresh_cache)
            valuation = value_bank(bank)
            rows.append(_build_row(bank, valuation))
        except RuntimeError as exc:
            logger.warning("Skip mean-reversion peer: code=%s error=%s", code, exc)
            failures.append({"stock_code": code, "error": str(exc)})
        except Exception as exc:
            logger.exception("Skip mean-reversion peer: code=%s", code)
            failures.append({"stock_code": code, "error": str(exc)})

    rows.sort(key=lambda row: row.mean_reversion_score, reverse=True)
    risky_count = sum(1 for row in rows if row.status == "risk_discount")
    investable_count = sum(1 for row in rows if row.status in {"high_conviction_reversion", "undervalued_watch"})
    income_candidate_count = sum(1 for row in rows if row.income_status in {"core_income", "income_watch"})
    yield_trap_count = sum(1 for row in rows if row.income_status == "yield_trap_risk")
    if not include_risky:
        rows = [row for row in rows if row.status != "risk_discount"]
    rows = [row.model_copy(update={"rank": index}) for index, row in enumerate(rows, start=1)]
    as_of = max((row.market_date for row in rows if row.market_date is not None), default=None)
    return BankMeanReversionOverviewResponse(
        module="bank_mean_reversion_overview",
        title="银行低估与均值回归排序",
        as_of_date=as_of,
        count=len(rows),
        investable_count=investable_count,
        income_candidate_count=income_candidate_count,
        yield_trap_count=yield_trap_count,
        risky_count=risky_count,
        failed_count=len(failures),
        results=rows,
        failures=failures,
        data_note=(
            "该排序用于侧边栏汇总看板，不构成投资建议。模型优先寻找 PB 处于自身历史低位、"
            "ROE/利润/资产质量/资本未触发硬风险、且有股息托底的银行；单纯便宜但基本面恶化的银行"
            "会标记为“风险型低估”并降低排序。股息安全分强调分红覆盖、资本缓冲、资产质量和盈利稳定；"
            "高股息低风险候选池优先选择股息率有吸引力但不过度依赖高分红率的银行。默认优先读取不晚于估值日期的本地快照以保证看板快速返回；"
            "需要逐家联网刷新时传入 refresh_cache=true。"
        ),
    )


def _load_overview_bank(
    code: str,
    valuation_date: date | None,
    requested_date: date,
    refresh_cache: bool,
) -> BankInput:
    if refresh_cache:
        return load_bank_input(code, valuation_date, refresh_cache=True)
    try:
        return _load_latest_cached_bank(code, requested_date)
    except RuntimeError as exc:
        if "缓存缺失" not in str(exc):
            raise
        logger.info("Overview cache missing; refreshing one bank: code=%s requested_date=%s", code, requested_date)
        return load_bank_input(code, requested_date, refresh_cache=True)


def _load_latest_cached_bank(code: str, requested_date: date) -> BankInput:
    """Use the freshest local snapshot on or before the requested date."""
    snapshot = _snapshot_path(code)
    if not snapshot.exists():
        raise RuntimeError("本地缓存缺失；请先单独分析该银行或使用 refresh_cache=true 刷新")
    try:
        with snapshot.open("r", encoding="utf-8", newline="") as file:
            available_dates = [
                date.fromisoformat(row["requested_date"])
                for row in csv.DictReader(file)
                if row.get("requested_date") and date.fromisoformat(row["requested_date"]) <= requested_date
            ]
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError("本地缓存不可读") from exc
    if not available_dates:
        raise RuntimeError("估值日期之前没有本地缓存快照")
    cached = _read_disk_cache(code, max(available_dates), allow_stale_current=True)
    if cached is None:
        raise RuntimeError("本地缓存快照无法恢复")
    return cached


def _build_row(bank: BankInput, valuation) -> BankMeanReversionRow:
    median_pb = _median_pb(bank.pb_history, years=5)
    pb_discount = 1 - bank.pb_current / median_pb if median_pb > 0 else 0
    median_price = bank.bps * median_pb
    mean_reversion_upside = median_price / bank.current_price - 1
    quality = _quality_score(bank)
    risk = _risk_score(bank, valuation.final_rating, valuation.risk_flags)
    probability = _reversion_probability(
        valuation.pb_percentile_5y,
        mean_reversion_upside,
        quality,
        risk,
        bank.dividend_yield,
    )
    score = _ranking_score(
        valuation.pb_percentile_5y,
        mean_reversion_upside,
        valuation.margin_of_safety,
        quality,
        risk,
        bank.dividend_yield,
    )
    status = _status(valuation.pb_percentile_5y, mean_reversion_upside, score, risk, valuation.final_rating)
    dividend_safety = _dividend_safety_score(bank, risk)
    stable_growth = _stable_growth_score(bank, risk)
    income_score = _income_candidate_score(bank, valuation.pb_percentile_5y, dividend_safety, stable_growth, risk)
    income_status = _income_status(bank, dividend_safety, stable_growth, income_score, risk)
    tags = _tags(bank, valuation.pb_percentile_5y, mean_reversion_upside, status)
    income_tags = _income_tags(bank, dividend_safety, stable_growth, income_status)
    return BankMeanReversionRow(
        rank=0,
        stock_code=bank.stock_code,
        stock_name=bank.stock_name,
        bank_profile=bank_profile(bank.stock_code),
        market_date=bank.market_date,
        current_price=round(bank.current_price, 4),
        current_pb=round(bank.pb_current, 4),
        pb_percentile_5y=round(valuation.pb_percentile_5y, 6),
        pb_discount_to_5y_median=round(pb_discount, 6),
        mean_reversion_upside=round(mean_reversion_upside, 6),
        upside_potential=valuation.upside_potential,
        margin_of_safety=valuation.margin_of_safety,
        dividend_yield=round(bank.dividend_yield, 6),
        roe=round(bank.roe, 6),
        profit_growth_yoy=round(bank.profit_growth_yoy, 6),
        npl_ratio=round(bank.npl_ratio, 6) if bank.npl_ratio is not None else None,
        provision_coverage=round(bank.provision_coverage, 6) if bank.provision_coverage is not None else None,
        cet1_ratio=round(bank.cet1_ratio, 6) if bank.cet1_ratio is not None else None,
        quality_score=round(quality, 2),
        risk_score=round(risk, 2),
        mean_reversion_score=round(score, 2),
        reversion_probability=round(probability, 6),
        dividend_safety_score=round(dividend_safety, 2),
        stable_growth_score=round(stable_growth, 2),
        income_candidate_score=round(income_score, 2),
        income_status=income_status,
        status=status,
        tags=tags,
        income_tags=income_tags,
        risk_flags=valuation.risk_flags,
        thesis=_thesis(status, tags),
    )


def _median_pb(pb_history: list[float], years: int) -> float:
    window = pb_history[-(years * 252):] or pb_history
    return statistics.median(window)


def _quality_score(bank: BankInput) -> float:
    score = 0.0
    if bank.roe >= .11:
        score += 28
    elif bank.roe >= .095:
        score += 24
    elif bank.roe >= .08:
        score += 20
    elif bank.roe >= .06:
        score += 10
    else:
        score += 2

    if bank.profit_growth_yoy >= .05:
        score += 18
    elif bank.profit_growth_yoy >= 0:
        score += 14
    elif bank.profit_growth_yoy >= -.05:
        score += 7

    if bank.npl_ratio is None:
        score += 7
    elif bank.npl_ratio <= .012:
        score += 14
    elif bank.npl_ratio <= .016:
        score += 12
    elif bank.npl_ratio <= .02:
        score += 8
    else:
        score += 2

    if bank.provision_coverage is None:
        score += 5
    elif bank.provision_coverage >= 2.5:
        score += 10
    elif bank.provision_coverage >= 1.8:
        score += 8
    elif bank.provision_coverage >= 1.5:
        score += 5
    else:
        score += 1

    if bank.cet1_ratio is None:
        score += 6
    elif bank.cet1_ratio >= .11:
        score += 12
    elif bank.cet1_ratio >= .095:
        score += 10
    elif bank.cet1_ratio >= .085:
        score += 6

    if bank.dividend_yield >= .055 and bank.payout_ratio <= .65:
        score += 10
    elif bank.dividend_yield >= .045 and bank.payout_ratio <= .75:
        score += 8
    elif bank.dividend_stable:
        score += 5

    if bank.npl_ratio_change <= 0:
        score += 3
    if bank.provision_coverage_change >= 0:
        score += 3
    if bank.nim_change >= -.001:
        score += 2
    return _clamp(score, 0, 100)


def _risk_score(bank: BankInput, final_rating: str, flags: list[str]) -> float:
    score = 0.0
    if bank.roe < .06:
        score += 25
    if bank.profit_growth_yoy < -.05:
        score += 20
    if bank.npl_ratio is not None and bank.npl_ratio > .02:
        score += 20
    if bank.npl_ratio_change > .002:
        score += 10
    if bank.provision_coverage is not None and bank.provision_coverage < 1.5:
        score += 15
    if bank.provision_coverage_change < 0:
        score += 8
    if bank.cet1_ratio is not None and bank.cet1_ratio < .085:
        score += 25
    if bank.cet1_ratio is not None and bank.cet1_ratio < .075:
        score += 10
    if bank.payout_ratio > .75 and bank.profit_growth_yoy < 0:
        score += 12
    if final_rating == "value_trap_risk":
        score += 30
    elif final_rating == "avoid":
        score += 40
    score += min(len(flags) * 4, 16)
    return _clamp(score, 0, 100)


def _reversion_probability(pb_percentile: float, upside: float, quality: float, risk: float, dividend_yield: float) -> float:
    cheapness = _clamp((.5 - pb_percentile) / .5, 0, 1)
    upside_factor = _clamp(upside, 0, .6) / .6
    dividend_factor = _clamp(dividend_yield, 0, .07) / .07
    probability = .15 + cheapness * .42 + quality / 100 * .28 + upside_factor * .18 + dividend_factor * .08 - risk / 100 * .30
    return _clamp(probability, .03, .92)


def _ranking_score(
    pb_percentile: float,
    upside: float,
    margin_of_safety: float,
    quality: float,
    risk: float,
    dividend_yield: float,
) -> float:
    cheapness = _clamp((.55 - pb_percentile) / .55, 0, 1)
    upside_factor = _clamp(upside, 0, .7) / .7
    margin_factor = _clamp(margin_of_safety, 0, .35) / .35
    dividend_factor = _clamp(dividend_yield, 0, .07) / .07
    score = 100 * (
        cheapness * .34
        + quality / 100 * .26
        + upside_factor * .18
        + margin_factor * .08
        + dividend_factor * .08
        - risk / 100 * .24
    )
    return _clamp(score, 0, 100)


def _dividend_safety_score(bank: BankInput, risk: float) -> float:
    score = 0.0
    if .04 <= bank.dividend_yield <= .075:
        score += 22
    elif .03 <= bank.dividend_yield < .04 or .075 < bank.dividend_yield <= .09:
        score += 15
    elif bank.dividend_yield > .09:
        score += 6

    if .25 <= bank.payout_ratio <= .55:
        score += 22
    elif .55 < bank.payout_ratio <= .7:
        score += 14
    elif bank.payout_ratio <= .75:
        score += 8
    else:
        score -= 10

    if bank.roe >= .10:
        score += 18
    elif bank.roe >= .08:
        score += 14
    elif bank.roe >= .065:
        score += 7

    if bank.profit_growth_yoy >= .02:
        score += 12
    elif bank.profit_growth_yoy >= 0:
        score += 9
    elif bank.profit_growth_yoy >= -.05:
        score += 3
    else:
        score -= 12

    if bank.cet1_ratio is None:
        score += 5
    elif bank.cet1_ratio >= .105:
        score += 12
    elif bank.cet1_ratio >= .095:
        score += 9
    elif bank.cet1_ratio >= .085:
        score += 4
    else:
        score -= 14

    if bank.provision_coverage is None:
        score += 3
    elif bank.provision_coverage >= 2.0:
        score += 8
    elif bank.provision_coverage >= 1.6:
        score += 5
    else:
        score -= 8

    if bank.npl_ratio is not None and bank.npl_ratio <= .016:
        score += 5
    if bank.dividend_stable:
        score += 5
    if bank.payout_ratio > .75 and bank.profit_growth_yoy < 0:
        score -= 14
    return _clamp(score - risk * .35, 0, 100)


def _stable_growth_score(bank: BankInput, risk: float) -> float:
    score = 0.0
    if bank.roe >= .12:
        score += 28
    elif bank.roe >= .10:
        score += 24
    elif bank.roe >= .085:
        score += 18
    elif bank.roe >= .07:
        score += 8

    if bank.profit_growth_yoy >= .05:
        score += 22
    elif bank.profit_growth_yoy >= .02:
        score += 18
    elif bank.profit_growth_yoy >= 0:
        score += 12
    elif bank.profit_growth_yoy >= -.05:
        score += 4

    if bank.npl_ratio is None:
        score += 6
    elif bank.npl_ratio <= .01:
        score += 14
    elif bank.npl_ratio <= .014:
        score += 11
    elif bank.npl_ratio <= .018:
        score += 6

    if bank.provision_coverage is None:
        score += 5
    elif bank.provision_coverage >= 2.5:
        score += 12
    elif bank.provision_coverage >= 1.8:
        score += 8
    elif bank.provision_coverage >= 1.5:
        score += 4

    if bank.cet1_ratio is None:
        score += 6
    elif bank.cet1_ratio >= .11:
        score += 12
    elif bank.cet1_ratio >= .095:
        score += 9
    elif bank.cet1_ratio >= .085:
        score += 4

    if bank.npl_ratio_change <= 0:
        score += 4
    if bank.provision_coverage_change >= 0:
        score += 3
    if bank.nim_change >= -.001:
        score += 3
    return _clamp(score - risk * .25, 0, 100)


def _income_candidate_score(bank: BankInput, pb_percentile: float, dividend_safety: float, stable_growth: float, risk: float) -> float:
    yield_score = _clamp((bank.dividend_yield - .025) / .05, 0, 1) * 100
    valuation_score = _clamp((.7 - pb_percentile) / .7, 0, 1) * 100
    payout_penalty = 18 if bank.payout_ratio > .75 else 8 if bank.payout_ratio > .68 else 0
    score = (
        dividend_safety * .38
        + stable_growth * .24
        + yield_score * .22
        + valuation_score * .16
        - risk * .22
        - payout_penalty
    )
    return _clamp(score, 0, 100)


def _income_status(bank: BankInput, dividend_safety: float, stable_growth: float, income_score: float, risk: float) -> str:
    if risk >= 35 or (bank.dividend_yield >= .055 and dividend_safety < 50) or (bank.payout_ratio > .75 and bank.profit_growth_yoy < 0):
        return "yield_trap_risk"
    if income_score >= 72 and dividend_safety >= 70 and stable_growth >= 60 and bank.dividend_yield >= .04:
        return "core_income"
    if income_score >= 58 and dividend_safety >= 58 and bank.dividend_yield >= .035:
        return "income_watch"
    return "not_income_candidate"


def _status(pb_percentile: float, upside: float, score: float, risk: float, final_rating: str) -> str:
    if risk >= 35 or final_rating in {"value_trap_risk", "avoid"}:
        return "risk_discount"
    if pb_percentile > .75 and upside < 0:
        return "overvalued"
    if score >= 72 and pb_percentile <= .25:
        return "high_conviction_reversion"
    if pb_percentile <= .40 and upside > 0:
        return "undervalued_watch"
    return "fair_value"


def _tags(bank: BankInput, pb_percentile: float, upside: float, status: str) -> list[str]:
    tags: list[str] = []
    if pb_percentile <= .10:
        tags.append("PB历史底部")
    elif pb_percentile <= .25:
        tags.append("PB偏低")
    elif pb_percentile >= .75:
        tags.append("PB偏高")

    if upside >= .20:
        tags.append("均值回归空间大")
    elif upside >= .08:
        tags.append("均值回归空间中等")

    if bank.roe >= .10:
        tags.append("ROE优质")
    elif bank.roe >= .08:
        tags.append("ROE稳健")
    elif bank.roe < .06:
        tags.append("ROE偏弱")

    if bank.profit_growth_yoy >= 0:
        tags.append("利润未负增长")
    else:
        tags.append("利润承压")

    if bank.dividend_yield >= .05:
        tags.append("股息托底")
    if bank.cet1_ratio is not None and bank.cet1_ratio >= .095:
        tags.append("资本缓冲充足")
    if bank.npl_ratio is not None and bank.npl_ratio <= .016 and bank.npl_ratio_change <= 0:
        tags.append("资产质量稳定")
    if status == "risk_discount":
        tags.append("风险型低估")
    elif status in {"high_conviction_reversion", "undervalued_watch"}:
        tags.append("等待银行股热点回归")
    return tags


def _income_tags(bank: BankInput, dividend_safety: float, stable_growth: float, income_status: str) -> list[str]:
    tags: list[str] = []
    if income_status == "core_income":
        tags.append("核心股息候选")
    elif income_status == "income_watch":
        tags.append("股息观察池")
    elif income_status == "yield_trap_risk":
        tags.append("高息陷阱风险")

    if bank.dividend_yield >= .06:
        tags.append("股息率较高")
    elif bank.dividend_yield >= .045:
        tags.append("股息有吸引力")

    if .25 <= bank.payout_ratio <= .6:
        tags.append("分红率适中")
    elif bank.payout_ratio > .75:
        tags.append("分红率偏高")

    if dividend_safety >= 70:
        tags.append("派息安全分高")
    if stable_growth >= 70:
        tags.append("稳定增长分高")
    if bank.cet1_ratio is not None and bank.cet1_ratio >= .095:
        tags.append("资本缓冲充足")
    if bank.profit_growth_yoy < 0:
        tags.append("利润负增长拖累")
    if bank.provision_coverage is not None and bank.provision_coverage < 1.6:
        tags.append("拨备偏薄")
    return tags


def _thesis(status: str, tags: list[str]) -> str:
    if status == "high_conviction_reversion":
        return "估值处于低位且经营质量未触发硬风险，属于更偏“便宜但没坏”的均值回归候选。"
    if status == "undervalued_watch":
        return "估值有修复空间，基本面仍需继续观察，适合放入低估跟踪池。"
    if status == "risk_discount":
        return "便宜主要可能来自经营或资产质量折价，需先确认风险收敛再谈均值回归。"
    if status == "overvalued":
        return "当前估值高于自身历史常态，均值回归方向未必有利。"
    joined = "、".join(tags[:3]) if tags else "估值和基本面信号不极端"
    return f"{joined}，暂未形成强低估回归信号。"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
