from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException
from ..models import BankInput, BatchValuationRequest, MonteCarloRequest, BankQuery, BatchBankQuery, SimpleMonteCarloRequest
from ..valuation.service import value_bank
from ..valuation.scenario import scenario_template
from ..valuation.monte_carlo import run_monte_carlo
from ..data_sources.bank_base import load_bank_input
from ..data_sources.industry_benchmark import get_industry_benchmark

router = APIRouter(prefix="/api/bank", tags=["bank-valuation"])
logger = logging.getLogger("bank_valuation.api")


@router.post("/valuation")
def valuation(query: BankQuery):
    """Simple API: only a bank code and optional historical valuation date."""
    try:
        logger.info("POST /valuation received: stock_code=%s valuation_date=%s", query.stock_code, query.valuation_date)
        bank = load_bank_input(query.stock_code, query.valuation_date, query.refresh_cache)
        return value_bank(bank)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /valuation failed: stock_code=%s", query.stock_code)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/valuation/advanced", include_in_schema=False)
def advanced_valuation(bank: BankInput):
    """Compatibility endpoint for callers that supply their own complete dataset."""
    try:
        return value_bank(bank)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/batch-valuation")
def batch_valuation(payload: BatchBankQuery):
    try:
        logger.info("POST /batch-valuation received: count=%d", len(payload.banks))
        results = [value_bank(load_bank_input(item.stock_code, item.valuation_date, item.refresh_cache)).model_dump() for item in payload.banks]
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /batch-valuation failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Ranking is an analytical composite, not an investment recommendation.
    results.sort(key=lambda row: (row["margin_of_safety"], row["upside_potential"]), reverse=True)
    for index, row in enumerate(results, start=1): row["valuation_rank"] = index
    return {"count": len(results), "results": results}


@router.post("/batch-valuation/advanced", include_in_schema=False)
def advanced_batch_valuation(payload: BatchValuationRequest):
    results = [value_bank(bank).model_dump() for bank in payload.banks]
    results.sort(key=lambda row: (row["margin_of_safety"], row["upside_potential"]), reverse=True)
    for index, row in enumerate(results, start=1): row["valuation_rank"] = index
    return {"count": len(results), "results": results}


@router.get("/scenario-template")
def get_scenario_template():
    return scenario_template()


@router.post("/industry-benchmark")
def industry_benchmark(query: BankQuery):
    """Cross-sectional live comparison against the A-share bank sample."""
    try:
        logger.info("POST /industry-benchmark received: stock_code=%s valuation_date=%s", query.stock_code, query.valuation_date)
        bank = load_bank_input(query.stock_code, query.valuation_date, query.refresh_cache)
        return get_industry_benchmark(bank)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /industry-benchmark failed: stock_code=%s", query.stock_code)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/monte-carlo")
def monte_carlo(payload: SimpleMonteCarloRequest):
    try:
        logger.info("POST /monte-carlo received: stock_code=%s years=%d simulations=%d", payload.stock_code, payload.years, payload.simulations)
        bank = load_bank_input(payload.stock_code, payload.valuation_date, payload.refresh_cache)
        return run_monte_carlo(bank, payload.years, payload.simulations, payload.seed)
    except (ValueError, RuntimeError) as exc:
        logger.exception("POST /monte-carlo failed: stock_code=%s", payload.stock_code)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/monte-carlo/advanced", include_in_schema=False)
def advanced_monte_carlo(payload: MonteCarloRequest):
    return run_monte_carlo(payload.bank, payload.years, payload.simulations, payload.seed)
