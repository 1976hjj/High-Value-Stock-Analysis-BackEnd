"""Pydantic contracts for the bank valuation API.

All rate fields use decimals: 8% is represented by ``0.08``.
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    pe_history: list[float | None] = Field(default_factory=list)
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
    history_years: int | None = Field(default=10, ge=1, le=40, description="历史行情年数；为 null 且 include_full_history=true 时拉取全历史")
    include_full_history: bool = Field(default=False, description="为 true 时尽量从上市以来拉取 PB/PE/收盘价历史")
    include_pe_history: bool = Field(default=True, description="为 true 时在历史行情中同时返回可取得的 PE")


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


IndustryId = Literal[
    "telecom",
    "hydro",
    "bank",
    "tollroad",
    "nuclear",
    "oilgas",
    "resources",
    "consumer",
]

ALL_INDUSTRY_IDS: tuple[IndustryId, ...] = (
    "telecom",
    "hydro",
    "bank",
    "tollroad",
    "nuclear",
    "oilgas",
    "resources",
    "consumer",
)


class CrossIndustryStrategyBacktestQuery(StrategyBacktestQuery):
    model_config = ConfigDict(extra="forbid")

    universe_mode: Literal["single", "selected", "all"] = "single"
    industry_ids: list[IndustryId] = Field(default_factory=lambda: ["bank"])
    industry_weighting: Literal["equal", "risk_parity", "score"] = "risk_parity"
    max_industry_weight: float = Field(default=.3, ge=.1, le=1)
    crisis_cash_buffer: float = Field(default=.1, ge=0, le=.3)
    holding_count: int = Field(default=12, ge=3, le=40)

    @field_validator("industry_ids")
    @classmethod
    def deduplicate_industries(cls, value: list[IndustryId]) -> list[IndustryId]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_universe(self):
        if self.universe_mode == "all":
            self.industry_ids = list(ALL_INDUSTRY_IDS)
        elif self.universe_mode == "single" and len(self.industry_ids) != 1:
            raise ValueError("单行业模式必须且只能选择一个行业")
        elif self.universe_mode == "selected" and not self.industry_ids:
            raise ValueError("多行业模式至少选择一个行业")

        if len(self.industry_ids) > 1:
            required = 1 - self.crisis_cash_buffer
            available = len(self.industry_ids) * self.max_industry_weight
            if available + 1e-9 < required:
                minimum = required / len(self.industry_ids)
                raise ValueError(
                    f"单行业权重上限不可行：当前至少需要 {minimum:.1%}，"
                    f"才能在保留 {self.crisis_cash_buffer:.1%} 危机缓冲仓后完成配置"
                )
            if self.holding_count < len(self.industry_ids):
                raise ValueError("持仓数量不能少于已选行业数量")
        return self


class IndustryAnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry_id: IndustryId
    stock_code: str
    valuation_date: date | None = None
    refresh_cache: bool = False


class IndustryFactorMetric(BaseModel):
    key: str
    label: str
    value: str
    score: float = Field(ge=0, le=100)
    status: Literal["strong", "stable", "watch", "risk"]
    source: str


class IndustryAnalysisScores(BaseModel):
    defense: float = Field(ge=0, le=100)
    income: float = Field(ge=0, le=100)
    quality: float = Field(ge=0, le=100)
    valuation: float = Field(ge=0, le=100)
    risk: float = Field(ge=0, le=100)
    overall: float = Field(ge=0, le=100)


class IndustryPanoramaMetric(BaseModel):
    key: str
    label: str
    value: str
    raw_value: float | None = None
    score: float = Field(ge=0, le=100)
    status: Literal["strong", "stable", "watch", "risk"]
    quality: Literal["reported", "derived", "proxy"]
    interpretation: str
    source: str


class IndustryPanoramaGroup(BaseModel):
    id: str
    title: str
    metrics: list[IndustryPanoramaMetric]


class IndustryPanorama(BaseModel):
    report_date: date
    published_date: date
    coverage_ratio: float = Field(ge=0, le=1)
    groups: list[IndustryPanoramaGroup]


class IndustryPriceScenario(BaseModel):
    id: Literal["bull", "base", "bear", "crisis"]
    name: str
    earnings_change: float
    target_pe: float
    dividend_yield_anchor: float | None = None
    price_low: float = Field(gt=0)
    price_mid: float = Field(gt=0)
    price_high: float = Field(gt=0)
    return_low: float
    return_mid: float
    return_high: float
    confidence: float = Field(ge=0, le=1)
    drivers: list[str]
    triggers: list[str]
    formula: str


class IndustryPriceProjection(BaseModel):
    model_name: str
    current_price: float = Field(gt=0)
    current_pe: float = Field(gt=0)
    implied_eps_ttm: float = Field(gt=0)
    market_date: date
    report_date: date
    scenarios: list[IndustryPriceScenario]
    base_value_mid: float = Field(gt=0)
    defensive_entry_price: float = Field(gt=0)
    conclusion: str
    assumptions: list[str]
    data_note: str


class IndustryAnalysisResponse(BaseModel):
    module: str = "industry_analysis"
    industry_id: IndustryId
    industry_name: str
    stock_code: str
    stock_name: str
    market_date: date
    valuation_date: date
    current_price: float = Field(gt=0)
    daily_change_pct: float | None = None
    current_pb: float | None = None
    current_pe: float | None = None
    pb_percentile_5y: float | None = Field(default=None, ge=0, le=1)
    pe_percentile_5y: float | None = Field(default=None, ge=0, le=1)
    valuation_metric: Literal["pb", "pe"]
    valuation_percentile: float = Field(ge=0, le=1)
    scores: IndustryAnalysisScores
    metrics: list[IndustryFactorMetric]
    panorama: IndustryPanorama | None = None
    price_projection: IndustryPriceProjection | None = None
    risk_flags: list[str]
    data_note: str


class IndustryRankingQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    industry_id: Literal["hydro", "consumer", "resources", "oilgas", "tollroad", "nuclear", "telecom"]
    valuation_date: date | None = None
    refresh_cache: bool = False


class IndustryRankingRow(BaseModel):
    rank: int = Field(ge=1)
    stock_code: str
    stock_name: str
    market_date: date
    report_date: date | None = None
    overall_score: float = Field(ge=0, le=100)
    defense_score: float = Field(ge=0, le=100)
    quality_score: float = Field(ge=0, le=100)
    income_score: float = Field(ge=0, le=100)
    valuation_score: float = Field(ge=0, le=100)
    risk_score: float = Field(ge=0, le=100)
    valuation_percentile: float = Field(ge=0, le=1)
    key_metrics: list[IndustryPanoramaMetric]
    risk_flags: list[str]


class IndustryRankingResponse(BaseModel):
    module: str = "industry_ranking"
    industry_id: Literal["hydro", "consumer", "resources", "oilgas", "tollroad", "nuclear", "telecom"]
    valuation_date: date
    result_count: int = Field(ge=0)
    results: list[IndustryRankingRow]
    failures: list[dict[str, str]]
    data_note: str


class BacktestPoint(BaseModel):
    date: date
    value: float


class BacktestYearReturn(BaseModel):
    year: int
    return_rate: float


class BacktestHolding(BaseModel):
    stock_code: str
    stock_name: str
    industry_id: str | None = None
    weight: float
    score: float
    dividend_yield: float | None
    risk_score: float
    entry_date: date | None = None
    holding_days: int = 0
    profit: float = 0.0
    price_profit: float = 0.0
    dividend_profit: float = 0.0
    position_value: float = 0.0
    cost_basis: float = 0.0
    profit_return: float = 0.0
    dividend_safety_score: float | None = None
    stable_growth_score: float | None = None
    quality_score: float | None = None
    valuation_percentile: float | None = None
    reversion_potential: float | None = None


class BacktestHoldingSnapshot(BaseModel):
    date: date
    holdings: list[BacktestHolding]


class BacktestSelectionSnapshot(BaseModel):
    """Actual candidates and factor values frozen at a scheduled rebalance."""
    date: date
    holdings: list[BacktestHolding]
    candidate_count: int
    cash_weight: float


class BacktestHoldingPriceSeries(BaseModel):
    """Price path and position facts for a currently held backtest security."""
    stock_code: str
    stock_name: str
    entry_date: date
    price_curve: list[BacktestPoint]
    start_price: float
    entry_price: float
    current_price: float
    high_price: float
    low_price: float
    price_return: float
    estimated_shares: float


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


class BacktestStockProfitContribution(BaseModel):
    """One security's realized contribution inside a reporting period."""
    stock_code: str
    stock_name: str
    industry_id: str | None = None
    net_profit: float
    price_profit: float
    dividend_profit: float
    transaction_cost: float
    return_contribution: float


class BacktestProfitContributionPeriod(BaseModel):
    """A reconciled calendar-year or full-backtest stock contribution ledger."""
    period_type: Literal["year", "total"]
    year: int | None = None
    start_date: date
    end_date: date
    start_value: float
    end_value: float
    net_profit: float
    stock_net_profit: float
    cash_profit: float
    return_rate: float
    reconciliation_error: float
    stocks: list[BacktestStockProfitContribution] = Field(default_factory=list)


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
    current_recommendation: BacktestSelectionSnapshot | None = None
    holding_snapshots: list[BacktestHoldingSnapshot] = Field(default_factory=list)
    selection_snapshots: list[BacktestSelectionSnapshot] = Field(default_factory=list)
    holding_price_series: list[BacktestHoldingPriceSeries] = Field(default_factory=list)
    yearly_profit_contributions: list[BacktestProfitContributionPeriod] = Field(default_factory=list)
    total_profit_contribution: BacktestProfitContributionPeriod | None = None


class StrategyBacktestResponse(BaseModel):
    module: str
    title: str
    start_date: date
    end_date: date
    strategy_count: int
    benchmark_note: str
    data_note: str
    benchmark_curve: list[BacktestPoint] = Field(default_factory=list)
    selected_industry_ids: list[str] = Field(default_factory=list)
    universe_size: int = 0
    industry_allocation: dict[str, float] = Field(default_factory=dict)
    failures: list[dict[str, str]] = Field(default_factory=list)
    results: list[StrategyBacktestResult]
