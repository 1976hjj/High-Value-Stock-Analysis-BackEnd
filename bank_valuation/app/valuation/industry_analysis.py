"""Point-in-time analysis for a non-bank industry stock."""
from __future__ import annotations

from datetime import date, timedelta
import math
import statistics

from ..data_sources.financial_snapshot import load_financial_snapshot
from ..data_sources.industry_catalog import INDUSTRY_CATALOG, get_stock_profile
from ..data_sources.security_history import load_market_histories
from ..models import (
    IndustryAnalysisQuery,
    IndustryAnalysisResponse,
    IndustryAnalysisScores,
    IndustryFactorMetric,
)
from .industry_panorama import build_industry_panorama
from .industry_price_projection import build_price_projection


def analyze_industry_stock(query: IndustryAnalysisQuery) -> IndustryAnalysisResponse:
    profile = get_stock_profile(query.industry_id, query.stock_code)
    industry = INDUSTRY_CATALOG[query.industry_id]
    valuation_date = query.valuation_date or date.today()
    history_start = valuation_date - timedelta(days=365 * 6)
    loaded = load_market_histories(
        [profile],
        history_start,
        valuation_date,
        adjustment="raw",
        refresh_cache=query.refresh_cache,
    )
    market = loaded.histories.get(profile.code)
    if not market:
        detail = loaded.failures[0]["error"] if loaded.failures else "没有可用行情"
        raise RuntimeError(f"{profile.name} 行业分析取数失败：{detail}")
    ordered = [(day, point) for day, point in sorted(market.items()) if day <= valuation_date]
    if not ordered:
        raise RuntimeError(f"{profile.name} 在估值日之前没有可用行情")
    market_date, current = ordered[-1]
    previous = ordered[-2][1] if len(ordered) >= 2 else None
    daily_change = current.close / previous.close - 1 if previous and previous.close > 0 else None
    adjusted_load = load_market_histories(
        [profile],
        history_start,
        valuation_date,
        adjustment="post",
        refresh_cache=query.refresh_cache,
    )
    adjusted_market = adjusted_load.histories.get(profile.code)
    if not adjusted_market:
        detail = adjusted_load.failures[0]["error"] if adjusted_load.failures else "没有可用复权行情"
        raise RuntimeError(f"{profile.name} 风险指标取数失败：{detail}")
    adjusted_ordered = [
        (day, point) for day, point in sorted(adjusted_market.items()) if day <= market_date
    ]
    trailing = adjusted_ordered[-252:]
    closes = [point.close for _, point in trailing]
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0]
    volatility = statistics.pstdev(returns) * math.sqrt(252) if len(returns) > 1 else .5
    peak = max(closes)
    drawdown = closes[-1] / peak - 1 if peak > 0 else -1
    momentum = closes[-1] / closes[0] - 1 if len(closes) > 1 and closes[0] > 0 else 0.0
    stability_score = _clamp(100 - volatility * 150 + drawdown * 35, 0, 100)

    metric_values = [
        point.pb if industry.valuation_metric == "pb" else point.pe
        for day, point in ordered
        if day >= market_date - timedelta(days=365 * 5)
    ]
    metric_values = [value for value in metric_values if value is not None and value > 0]
    current_metric = current.pb if industry.valuation_metric == "pb" else current.pe
    valuation_percentile = (
        sum(1 for value in metric_values if value <= current_metric) / len(metric_values)
        if metric_values and current_metric is not None
        else .5
    )
    valuation_score = (1 - valuation_percentile) * 100
    defense_score = profile.defense_score * .72 + stability_score * .28
    quality_score = profile.quality_score * .82 + _clamp(50 + momentum * 60, 0, 100) * .18
    income_score = profile.income_score
    panorama = None
    panorama_error = ""
    if query.industry_id in {"hydro", "consumer", "resources", "oilgas", "tollroad", "nuclear", "telecom"}:
        try:
            financial = load_financial_snapshot(profile, valuation_date, refresh_cache=query.refresh_cache)
            panorama = build_industry_panorama(
                query.industry_id,
                financial,
                current_price=current.close,
                current_pe=current.pe,
                current_pb=current.pb,
                valuation_percentile=valuation_percentile,
                volatility=volatility,
                drawdown=drawdown,
            )
            if panorama is not None:
                group_scores = {
                    group.id: statistics.fmean(metric.score for metric in group.metrics)
                    for group in panorama.groups
                    if group.metrics
                }
                if query.industry_id == "hydro":
                    operating_score = statistics.fmean(
                        [group_scores.get("asset", 65), group_scores.get("operation", 65)]
                    )
                    finance_score = group_scores.get("finance", 65)
                    return_score = group_scores.get("return", 65)
                elif query.industry_id == "consumer":
                    operating_score = statistics.fmean(
                        [group_scores.get("brand", 65), group_scores.get("channel", 65)]
                    )
                    finance_score = group_scores.get("finance", 65)
                    return_score = group_scores.get("value", 65)
                elif query.industry_id == "resources":
                    operating_score = statistics.fmean(
                        [group_scores.get("cycle", 65), group_scores.get("cost", 65)]
                    )
                    finance_score = group_scores.get("balance", 65)
                    return_score = group_scores.get("return", 65)
                elif query.industry_id == "oilgas":
                    operating_score = statistics.fmean(
                        [group_scores.get("upstream", 65), group_scores.get("integration", 65)]
                    )
                    finance_score = group_scores.get("capital", 65)
                    return_score = group_scores.get("return", 65)
                elif query.industry_id == "tollroad":
                    operating_score = statistics.fmean(
                        [group_scores.get("traffic", 65), group_scores.get("asset", 65)]
                    )
                    finance_score = group_scores.get("finance", 65)
                    return_score = group_scores.get("return", 65)
                elif query.industry_id == "nuclear":
                    operating_score = statistics.fmean(
                        [group_scores.get("generation", 65), group_scores.get("construction", 65)]
                    )
                    finance_score = group_scores.get("finance", 65)
                    return_score = group_scores.get("return", 65)
                else:
                    operating_score = statistics.fmean(
                        [group_scores.get("subscriber", 65), group_scores.get("network", 65)]
                    )
                    finance_score = group_scores.get("finance", 65)
                    return_score = group_scores.get("return", 65)
                defense_score = (
                    profile.defense_score * .36
                    + stability_score * .22
                    + operating_score * .20
                    + finance_score * .22
                )
                quality_score = profile.quality_score * .24 + operating_score * .31 + finance_score * .45
                income_score = profile.income_score * .34 + return_score * .66
        except Exception as exc:
            panorama_error = str(exc)
    risk_score = _clamp(100 - (defense_score * .68 + stability_score * .32), 0, 100)
    overall = _clamp(
        defense_score * .30
        + income_score * .18
        + quality_score * .24
        + valuation_score * .18
        + (100 - risk_score) * .10,
        0,
        100,
    )
    price_projection = (
        build_price_projection(
            query.industry_id,
            panorama,
            current_price=current.close,
            current_pe=current.pe,
            market_date=market_date,
            quality_score=quality_score,
            valuation_percentile=valuation_percentile,
            stock_code=profile.code.split(".")[1],
        )
        if panorama is not None
        else None
    )

    metrics = [
        IndustryFactorMetric(
            key="defense_prior",
            label="业务抗危机",
            value=f"{profile.defense_score:.0f}/100",
            score=round(defense_score, 2),
            status=_status(defense_score),
            source="行业研究先验 + 近一年价格稳定性",
        ),
        IndustryFactorMetric(
            key="valuation_percentile",
            label=f"{industry.valuation_metric.upper()} 历史分位",
            value=f"{valuation_percentile:.1%}",
            score=round(valuation_score, 2),
            status=_status(valuation_score),
            source="Baostock 点时日线估值",
        ),
        IndustryFactorMetric(
            key="volatility",
            label="近一年年化波动",
            value=f"{volatility:.1%}",
            score=round(stability_score, 2),
            status=_status(stability_score),
            source="Baostock 后复权收盘价",
        ),
        IndustryFactorMetric(
            key="drawdown",
            label="近一年峰值回撤",
            value=f"{drawdown:.1%}",
            score=round(_clamp(100 + drawdown * 180, 0, 100), 2),
            status=_status(_clamp(100 + drawdown * 180, 0, 100)),
            source="Baostock 后复权收盘价",
        ),
    ]
    risk_flags: list[str] = []
    if valuation_percentile >= .8:
        risk_flags.append(f"{industry.valuation_metric.upper()} 位于近五年高分位，业务稳定不等于估值安全")
    if volatility >= .35:
        risk_flags.append("近一年波动率较高，危机期行业相关性可能快速上升")
    if drawdown <= -.25:
        risk_flags.append("近一年从峰值回撤超过 25%，需要核对行业基本面是否同步恶化")
    actual_dividend_yield = next(
        (
            metric.raw_value
            for group in panorama.groups
            for metric in group.metrics
            if metric.key == "dividend_yield"
        ),
        None,
    ) if panorama is not None else None
    if actual_dividend_yield is not None and actual_dividend_yield < .03:
        risk_flags.append("该标的不是纯高股息资产，应主要依赖质量与长期现金流回报")
    if panorama is not None:
        automated_risks = [
            metric
            for group in panorama.groups
            for metric in group.metrics
            if metric.status == "risk"
        ]
        risk_flags.extend(
            f"自动财务预警：{metric.label} {metric.value}"
            for metric in automated_risks[:3]
        )
    elif panorama_error:
        risk_flags.append(f"财务全景暂不可用：{panorama_error}")

    return IndustryAnalysisResponse(
        industry_id=query.industry_id,
        industry_name=industry.name,
        stock_code=profile.code.split(".")[1],
        stock_name=profile.name,
        market_date=market_date,
        valuation_date=valuation_date,
        current_price=current.close,
        daily_change_pct=daily_change,
        current_pb=current.pb,
        current_pe=current.pe,
        valuation_metric=industry.valuation_metric,
        valuation_percentile=round(valuation_percentile, 6),
        scores=IndustryAnalysisScores(
            defense=round(defense_score, 2),
            income=round(income_score, 2),
            quality=round(quality_score, 2),
            valuation=round(valuation_score, 2),
            risk=round(risk_score, 2),
            overall=round(overall, 2),
        ),
        metrics=metrics,
        panorama=panorama,
        price_projection=price_projection,
        risk_flags=risk_flags,
        data_note=(
            "价格、PB/PE、波动和回撤按所选估值日计算。"
            + (
                f"自动财务全景采用 {panorama.report_date} 报告（{panorama.published_date} 披露），"
                "reported 为财报原值、derived 为可复算派生值、proxy 为明确标注的行业代理指标；"
                "来水、终端动销、经销商库存及商品现货价格仍需经营或商品数据源复核。"
                if panorama is not None
                else "业务防御、分红与质量暂使用显式研究先验。"
            )
        ),
    )


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _status(score: float) -> str:
    if score >= 82:
        return "strong"
    if score >= 65:
        return "stable"
    if score >= 45:
        return "watch"
    return "risk"
