from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..data_sources.industry_catalog import INDUSTRY_CATALOG, INDUSTRY_ORDER
from ..models import CrossIndustryStrategyBacktestQuery, IndustryAnalysisQuery, IndustryRankingQuery
from ..valuation.cross_industry_backtest import run_cross_industry_backtest
from ..valuation.industry_analysis import analyze_industry_stock
from ..valuation.industry_ranking import run_industry_ranking


router = APIRouter(prefix="/api", tags=["multi-industry-strategy"])
logger = logging.getLogger("bank_valuation.strategy_api")


@router.get("/strategy/universe")
def strategy_universe():
    return {
        "industry_count": len(INDUSTRY_ORDER),
        "stock_count": sum(len(INDUSTRY_CATALOG[item].stocks) for item in INDUSTRY_ORDER),
        "industries": [
            {
                "industry_id": industry_id,
                "name": INDUSTRY_CATALOG[industry_id].name,
                "defensive_score": INDUSTRY_CATALOG[industry_id].defensive_score,
                "risk_budget": INDUSTRY_CATALOG[industry_id].risk_budget,
                "primary_risk": INDUSTRY_CATALOG[industry_id].primary_risk,
                "valuation_metric": INDUSTRY_CATALOG[industry_id].valuation_metric,
                "stocks": [
                    {
                        "stock_code": stock.code.split(".")[1],
                        "stock_name": stock.name,
                        "defense_score": stock.defense_score,
                        "income_score": stock.income_score,
                        "quality_score": stock.quality_score,
                    }
                    for stock in INDUSTRY_CATALOG[industry_id].stocks
                ],
            }
            for industry_id in INDUSTRY_ORDER
        ],
    }


@router.post("/industry/analysis")
def industry_analysis(query: IndustryAnalysisQuery):
    try:
        logger.info(
            "POST /industry/analysis: industry=%s stock=%s date=%s refresh=%s",
            query.industry_id,
            query.stock_code,
            query.valuation_date,
            query.refresh_cache,
        )
        return analyze_industry_stock(query)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /industry/analysis failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/industry/ranking")
def industry_ranking(query: IndustryRankingQuery):
    try:
        logger.info(
            "POST /industry/ranking: industry=%s date=%s refresh=%s",
            query.industry_id,
            query.valuation_date,
            query.refresh_cache,
        )
        return run_industry_ranking(query)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /industry/ranking failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/strategy/backtest")
def cross_industry_strategy_backtest(query: CrossIndustryStrategyBacktestQuery):
    try:
        logger.info(
            "POST /strategy/backtest: mode=%s industries=%s years=%s holdings=%s",
            query.universe_mode,
            query.industry_ids,
            query.years,
            query.holding_count,
        )
        return run_cross_industry_backtest(query)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /strategy/backtest failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/strategy/backtest/prepare")
def prepare_cross_industry_strategy_backtest(query: CrossIndustryStrategyBacktestQuery):
    try:
        logger.info(
            "POST /strategy/backtest/prepare: industries=%s years=%s",
            query.industry_ids,
            query.years,
        )
        return run_cross_industry_backtest(query, refresh_cache=True)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /strategy/backtest/prepare failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
