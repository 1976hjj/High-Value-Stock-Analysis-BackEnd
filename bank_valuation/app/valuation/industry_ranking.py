"""Automated peer ranking for hydro, consumer and resource leaders."""
from __future__ import annotations

from datetime import date
import statistics

from ..data_sources.industry_catalog import INDUSTRY_CATALOG
from ..models import (
    IndustryAnalysisQuery,
    IndustryRankingQuery,
    IndustryRankingResponse,
    IndustryRankingRow,
)
from .industry_analysis import analyze_industry_stock


_KEYS = {
    "hydro": (
        "profit_growth_proxy",
        "cfo_to_np",
        "interest_cover",
        "liability_ratio",
        "dividend_yield",
        "valuation_percentile",
    ),
    "consumer": (
        "revenue_growth",
        "profit_growth",
        "cfo_to_np",
        "roe_annualized",
        "dividend_yield",
        "valuation_percentile",
    ),
    "resources": (
        "commodity_revenue_proxy",
        "commodity_profit_proxy",
        "cfo_to_np",
        "liability_ratio",
        "dividend_yield",
        "valuation_percentile",
    ),
    "oilgas": (
        "oil_revenue_proxy",
        "oil_profit_proxy",
        "cfo_to_np",
        "liability_ratio",
        "dividend_yield",
        "valuation_percentile",
    ),
    "tollroad": (
        "traffic_revenue_proxy",
        "traffic_profit_proxy",
        "cfo_to_np",
        "liability_ratio",
        "dividend_yield",
        "valuation_percentile",
    ),
    "nuclear": (
        "nuclear_generation_proxy",
        "nuclear_profit_proxy",
        "cfo_to_np",
        "liability_ratio",
        "dividend_yield",
        "valuation_percentile",
    ),
    "telecom": (
        "telecom_revenue_proxy",
        "telecom_profit_proxy",
        "cfo_to_np",
        "asset_growth",
        "dividend_yield",
        "valuation_percentile",
    ),
}


def _mean(values: list[float], fallback: float = 50.0) -> float:
    return statistics.fmean(values) if values else fallback


def _clamp(value: float) -> float:
    return min(100.0, max(0.0, value))


def run_industry_ranking(query: IndustryRankingQuery) -> IndustryRankingResponse:
    valuation_date = query.valuation_date or date.today()
    rows: list[IndustryRankingRow] = []
    failures: list[dict[str, str]] = []
    for stock in INDUSTRY_CATALOG[query.industry_id].stocks:
        code = stock.code.split(".")[1]
        try:
            analysis = analyze_industry_stock(
                IndustryAnalysisQuery(
                    industry_id=query.industry_id,
                    stock_code=code,
                    valuation_date=valuation_date,
                    refresh_cache=query.refresh_cache,
                )
            )
            if analysis.panorama is None:
                raise RuntimeError("自动财务全景不可用")
            group_scores = {
                group.id: _mean([metric.score for metric in group.metrics])
                for group in analysis.panorama.groups
            }
            if query.industry_id == "hydro":
                operating = _mean([group_scores.get("asset", 50), group_scores.get("operation", 50)])
                finance = group_scores.get("finance", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("asset", 50), finance])
                quality = _mean([group_scores.get("operation", 50), finance])
            elif query.industry_id == "consumer":
                operating = _mean([group_scores.get("brand", 50), group_scores.get("channel", 50)])
                finance = group_scores.get("finance", 50)
                shareholder = group_scores.get("value", 50)
                defense = _mean([group_scores.get("brand", 50), finance])
                quality = _mean([operating, finance])
            elif query.industry_id == "resources":
                operating = _mean([group_scores.get("cycle", 50), group_scores.get("cost", 50)])
                finance = group_scores.get("balance", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("cost", 50), finance])
                quality = _mean([operating, finance])
            elif query.industry_id == "oilgas":
                operating = _mean([group_scores.get("upstream", 50), group_scores.get("integration", 50)])
                finance = group_scores.get("capital", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("integration", 50), finance])
                quality = _mean([operating, finance])
            elif query.industry_id == "tollroad":
                operating = _mean([group_scores.get("traffic", 50), group_scores.get("asset", 50)])
                finance = group_scores.get("finance", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("traffic", 50), finance])
                quality = _mean([operating, finance])
            elif query.industry_id == "nuclear":
                operating = _mean([group_scores.get("generation", 50), group_scores.get("construction", 50)])
                finance = group_scores.get("finance", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("generation", 50), finance])
                quality = _mean([operating, finance])
            else:
                operating = _mean([group_scores.get("subscriber", 50), group_scores.get("network", 50)])
                finance = group_scores.get("finance", 50)
                shareholder = group_scores.get("return", 50)
                defense = _mean([group_scores.get("subscriber", 50), finance])
                quality = _mean([operating, finance])
            risk = _clamp(100 - (finance * .50 + operating * .25 + shareholder * .25))
            overall = _clamp(
                operating * .25
                + finance * .30
                + shareholder * .20
                + analysis.scores.valuation * .15
                + (100 - risk) * .10
            )
            metrics = {
                metric.key: metric
                for group in analysis.panorama.groups
                for metric in group.metrics
            }
            rows.append(
                IndustryRankingRow(
                    rank=1,
                    stock_code=analysis.stock_code,
                    stock_name=analysis.stock_name,
                    market_date=analysis.market_date,
                    report_date=analysis.panorama.report_date,
                    overall_score=round(overall, 2),
                    defense_score=round(defense, 2),
                    quality_score=round(quality, 2),
                    income_score=round(shareholder, 2),
                    valuation_score=analysis.scores.valuation,
                    risk_score=round(risk, 2),
                    valuation_percentile=analysis.valuation_percentile,
                    key_metrics=[metrics[key] for key in _KEYS[query.industry_id] if key in metrics],
                    risk_flags=analysis.risk_flags,
                )
            )
        except Exception as exc:
            failures.append({"stock_code": code, "stock_name": stock.name, "error": str(exc)})
    rows.sort(key=lambda row: (-row.overall_score, row.risk_score, row.stock_code))
    ranked = [row.model_copy(update={"rank": index}) for index, row in enumerate(rows, start=1)]
    return IndustryRankingResponse(
        industry_id=query.industry_id,
        valuation_date=valuation_date,
        result_count=len(ranked),
        results=ranked,
        failures=failures,
        data_note=(
            "排名仅使用估值日前已披露财报、实际分红、点时估值和后复权风险数据。"
            "水电来水、消费终端动销、资源商品景气、油气价格/炼化缓冲、公路车流费率、核电量价/投产进度及电信ARPU/资本开支使用明确标注的财务代理指标；不使用回测结果。"
        ),
    )
