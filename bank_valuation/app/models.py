"""Pydantic contracts for the bank valuation API.

All rate fields use decimals: 8% is represented by ``0.08``.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, field_validator


class BankInput(BaseModel):
    stock_code: str
    stock_name: str
    current_price: float = Field(gt=0)
    daily_change_pct: float | None = Field(default=None, ge=-1, le=10)
    bps: float = Field(gt=0, description="Book value per share")
    eps: float
    roe: float = Field(ge=-1, le=1)
    net_profit: float
    profit_growth_yoy: float = Field(ge=-2, le=5)
    dividend_per_share: float = Field(ge=0)
    payout_ratio: float = Field(ge=0, le=1.5)
    dividend_yield: float = Field(ge=0, le=2)
    pb_current: float = Field(gt=0)
    pe_current: float | None = Field(default=None, gt=0)
    pb_history: list[float] = Field(min_length=1)
    nim: float | None = Field(default=None, ge=-0.2, le=0.2)
    npl_ratio: float | None = Field(default=None, ge=0, le=1)
    provision_coverage: float | None = Field(default=None, ge=0, le=20)
    provision_coverage_report_date: date | None = None
    provision_coverage_source: str | None = None
    cet1_ratio: float | None = Field(default=None, ge=0, le=1)
    capital_adequacy_ratio: float | None = Field(default=None, ge=0, le=1)
    net_interest_spread: float | None = Field(default=None, ge=-0.2, le=0.2)
    loan_provision_ratio: float | None = Field(default=None, ge=0, le=1)
    loan_to_deposit_ratio: float | None = Field(default=None, ge=0, le=2)
    bank_special_metrics_report_date: date | None = None
    bank_special_metrics_source: str | None = None
    risk_free_rate: float = Field(ge=-0.1, le=1)
    equity_risk_premium: float = Field(ge=0, le=1)
    beta: float = Field(ge=0, le=5)
    long_term_growth: float = Field(ge=-0.2, le=0.2)
    market_date: date | None = None
    financial_report_date: date | None = None
    pb_history_dates: list[date] = Field(default_factory=list)
    price_history: list[float] = Field(default_factory=list)
    # Optional trend fields let callers enrich risk analysis without changing core inputs.
    roe_trend: float = 0.0
    nim_change: float = 0.0
    npl_ratio_change: float = 0.0
    provision_coverage_change: float = 0.0
    dividend_stable: bool = True

    @field_validator("pb_history")
    @classmethod
    def valid_pb_history(cls, values: list[float]) -> list[float]:
        if any(value <= 0 for value in values):
            raise ValueError("pb_history values must all be positive")
        return values


class ScenarioConfig(BaseModel):
    name: Literal["bull", "base", "bear", "crisis"]
    roe_range: tuple[float, float]
    profit_growth_range: tuple[float, float]
    target_pb_range: tuple[float, float]
    trigger_conditions: list[str]


class MonteCarloRequest(BaseModel):
    bank: BankInput
    years: Literal[3, 5] = 5
    simulations: int = Field(default=10_000, ge=100, le=100_000)
    seed: int | None = 42


class RiskDriver(BaseModel):
    category: str
    status: Literal["stable", "watch", "risk"]
    current_reading: str
    why_it_matters: str
    deterioration_signal: str
    data_source: str


class RiskAnalysis(BaseModel):
    current_assessment: str
    drivers: list[RiskDriver]
    market_conditions: list[str]


class StressTestItem(BaseModel):
    name: str
    stressed_price: float | None
    downside: float | None
    severity: Literal["green", "yellow", "red"]
    note: str


class DefensiveDecision(BaseModel):
    buy_wait_price: float
    buy_wait_gap: float
    buy_wait_status: Literal["reached", "near", "wait", "avoid"]
    buy_wait_reason: str
    risk_light: Literal["green", "yellow", "red"]
    risk_light_label: str
    risk_light_reasons: list[str]
    stress_tests: list[StressTestItem]


class BankProfile(BaseModel):
    bank_type: str
    brief: str
    logo_text: str
    logo_tone: str


class ValuationResponse(BaseModel):
    stock_code: str
    stock_name: str
    bank_profile: BankProfile
    current_price: float
    daily_change_pct: float | None = None
    current_pb: float
    market_date: date | None = None
    financial_report_date: date | None = None
    cost_of_equity: float
    pb_percentile_3y: float
    pb_percentile_5y: float
    pb_percentile_10y: float
    fair_pb_pb_roe: float
    pb_roe_fair_price: float
    dividend_floor_price: float | None
    dividend_stress_floor_price: float | None
    residual_income_price: float
    residual_income_prices: dict[str, float]
    input_snapshot: dict[str, float | str | bool | None]
    pb_history_chart: list[dict[str, float | str | None]]
    scenarios: dict[str, dict]
    scenario_probabilities: dict[str, float]
    upside_potential: float
    downside_risk: float
    margin_of_safety: float
    risk_flags: list[str]
    risk_analysis: RiskAnalysis
    defensive_decision: DefensiveDecision
    final_rating: Literal["deep_value", "watch", "hold_income", "value_trap_risk", "avoid"]


class BatchValuationRequest(BaseModel):
    banks: list[BankInput] = Field(min_length=1, max_length=500)


class BankQuery(BaseModel):
    """Minimal public request. Data and standard assumptions are resolved server-side."""
    stock_code: str = Field(description="例如 601398、sh.601398 或 sz.000001")
    valuation_date: date | None = Field(default=None, description="估值日期；留空时使用最近可获得交易日")
    refresh_cache: bool = Field(default=False, description="为 true 时忽略本地CSV缓存并重新查询 Baostock")


class BatchBankQuery(BaseModel):
    banks: list[BankQuery] = Field(min_length=1, max_length=100)


class SimpleMonteCarloRequest(BankQuery):
    years: Literal[3, 5] = 5
    simulations: int = Field(default=10_000, ge=100, le=100_000)
    seed: int | None = 42


class IndustryMetric(BaseModel):
    current_value: float
    average: float
    median: float
    p25: float
    p75: float
    percentile: float
    sample_size: int


class IndustryBenchmarkResponse(BaseModel):
    industry_name: str
    as_of_date: date
    sample_size: int
    metrics: dict[str, IndustryMetric]
    data_note: str


class BankMeanReversionQuery(BaseModel):
    """Request for the all-bank undervaluation and mean-reversion overview."""
    valuation_date: date | None = Field(default=None, description="估值日期；留空时使用最近可获得交易日")
    refresh_cache: bool = Field(default=False, description="为 true 时强制刷新本地缓存")
    include_risky: bool = Field(default=True, description="是否在结果中保留经营风险型低估银行")


class BankMeanReversionRow(BaseModel):
    rank: int
    stock_code: str
    stock_name: str
    bank_profile: BankProfile
    market_date: date | None = None
    current_price: float
    current_pb: float
    pb_percentile_5y: float
    pb_discount_to_5y_median: float
    mean_reversion_upside: float
    upside_potential: float
    margin_of_safety: float
    dividend_yield: float
    roe: float
    profit_growth_yoy: float
    npl_ratio: float | None = None
    provision_coverage: float | None = None
    cet1_ratio: float | None = None
    quality_score: float
    risk_score: float
    mean_reversion_score: float
    reversion_probability: float
    dividend_safety_score: float
    stable_growth_score: float
    income_candidate_score: float
    income_status: Literal["core_income", "income_watch", "yield_trap_risk", "not_income_candidate"]
    status: Literal["high_conviction_reversion", "undervalued_watch", "fair_value", "risk_discount", "overvalued"]
    tags: list[str]
    income_tags: list[str]
    risk_flags: list[str]
    thesis: str


class BankMeanReversionOverviewResponse(BaseModel):
    module: str
    title: str
    as_of_date: date | None = None
    count: int
    investable_count: int
    income_candidate_count: int
    yield_trap_count: int
    risky_count: int
    failed_count: int
    results: list[BankMeanReversionRow]
    failures: list[dict[str, str]]
    data_note: str


class StrategyBacktestQuery(BaseModel):
    years: int = Field(default=10, ge=3, le=15)
    start_date: date | None = None
    end_date: date | None = None
    rebalance_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] = "quarterly"
    holding_count: int = Field(default=10, ge=3, le=20)
    min_dividend_yield: float = Field(default=0.03, ge=0, le=0.2)
    min_dividend_safety: float = Field(default=55, ge=0, le=100)
    min_stable_growth: float = Field(default=45, ge=0, le=100)
    max_risk_score: float = Field(default=45, ge=0, le=100)
    max_payout_ratio: float = Field(default=0.8, ge=0, le=1.5)
    dividend_weight: float = Field(default=0.25, ge=0, le=1)
    safety_weight: float = Field(default=0.25, ge=0, le=1)
    growth_weight: float = Field(default=0.2, ge=0, le=1)
    valuation_weight: float = Field(default=0.2, ge=0, le=1)
    risk_penalty_weight: float = Field(default=0.15, ge=0, le=1)
    initial_capital: float = Field(default=1.0, gt=0)
    commission_rate: float = Field(default=0.0002, ge=0, le=0.01)
    stamp_duty_rate: float = Field(default=0.0005, ge=0, le=0.02)
    transfer_fee_rate: float = Field(default=0.00001, ge=0, le=0.01)
    slippage_rate: float = Field(default=0.0001, ge=0, le=0.02)
    cash_yield: float = Field(default=0.015, ge=0, le=0.1)


class BacktestPoint(BaseModel):
    date: date
    value: float


class BacktestYearReturn(BaseModel):
    year: int
    return_rate: float


class BacktestHolding(BaseModel):
    stock_code: str
    stock_name: str
    weight: float
    score: float
    dividend_yield: float
    risk_score: float
    entry_date: date | None = None
    holding_days: int = 0
    profit: float = 0.0


class BacktestHoldingSnapshot(BaseModel):
    date: date
    holdings: list[BacktestHolding]


class BacktestMetrics(BaseModel):
    total_return: float
    annualized_return: float
    max_drawdown: float
    max_drawdown_date: date
    recovery_date: date | None
    recovery_days: int | None
    volatility: float
    sharpe: float | None
    calmar: float | None
    win_year_rate: float
    annual_dividend_return: float
    turnover: float
    rebalance_count: int
    total_transaction_cost: float = 0.0


class StrategyBacktestResult(BaseModel):
    strategy_id: Literal["income_core", "value_reversion", "defensive_rotation"]
    strategy_name: str
    description: str
    metrics: BacktestMetrics
    equity_curve: list[BacktestPoint]
    drawdown_curve: list[BacktestPoint]
    transaction_cost_curve: list[BacktestPoint] = Field(default_factory=list)
    yearly_returns: list[BacktestYearReturn]
    current_holdings: list[BacktestHolding]
    holding_snapshots: list[BacktestHoldingSnapshot] = Field(default_factory=list)


class StrategyBacktestResponse(BaseModel):
    module: str
    title: str
    start_date: date
    end_date: date
    strategy_count: int
    benchmark_note: str
    data_note: str
    benchmark_curve: list[BacktestPoint] = Field(default_factory=list)
    results: list[StrategyBacktestResult]
