"""Cross-industry defensive-value strategy backtest.

Returns use post-adjusted daily closes, so dividends, splits and bonus shares
are represented once. Company quality scores are explicit research priors;
all price, valuation, volatility and drawdown factors are point-in-time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import math
import statistics

from ..data_sources.industry_catalog import (
    INDUSTRY_CATALOG,
    IndustryStockProfile,
    profiles_for_industries,
)
from ..data_sources.security_history import (
    MarketHistoryLoad,
    SecurityMarketPoint,
    load_market_histories,
)
from ..models import (
    BacktestHolding,
    BacktestHoldingPriceSeries,
    BacktestHoldingSnapshot,
    BacktestPoint,
    BacktestSelectionSnapshot,
    CrossIndustryStrategyBacktestQuery,
    StrategyBacktestResponse,
    StrategyBacktestResult,
)
from .strategy_backtest import (
    DividendEvent,
    MIN_BACKTEST_TRADING_DAYS,
    _advance_rebalance,
    _fetch_dividend_events,
    _metrics,
    _holding_price_series,
    _read_dividend_events,
    _sample_curve,
    _write_dividend_events_cache,
    _yearly_returns,
)


@dataclass(frozen=True)
class CrossBacktestData:
    profile: IndustryStockProfile
    market: dict[date, SecurityMarketPoint]
    raw_market: dict[date, SecurityMarketPoint]
    dividends: list[DividendEvent]
    dividend_data_available: bool


@dataclass(frozen=True)
class CrossCandidate:
    code: str
    name: str
    industry_id: str
    score: float
    dividend_yield: float | None
    risk_score: float
    volatility: float
    dividend_safety_score: float = 0.0
    stable_growth_score: float = 0.0
    quality_score: float = 0.0
    valuation_percentile: float = 0.5


CROSS_STRATEGIES = {
    "income_core": {
        "name": "跨行业高股息质量核心",
        "description": "先在各行业内过滤分红安全、经营质量与尾部风险，再按行业风险预算配置。",
    },
    "value_reversion": {
        "name": "跨行业股息 + 估值修复",
        "description": "在行业内估值分位较低的候选中寻找均值回归，同时限制单行业集中度。",
    },
    "defensive_rotation": {
        "name": "跨行业危机防守轮动",
        "description": "提高低波动和抗危机因子权重，并保留用户设置的危机缓冲仓。",
    },
}


def run_cross_industry_backtest(
    query: CrossIndustryStrategyBacktestQuery,
    *,
    refresh_cache: bool = False,
) -> StrategyBacktestResponse:
    requested_end = query.end_date or date.today()
    simulation_start = query.start_date or (
        requested_end - timedelta(days=365 * query.years + 10)
    )
    history_start = simulation_start - timedelta(days=365 * 5 + 10)
    profiles = profiles_for_industries(list(query.industry_ids))
    loaded = load_market_histories(
        profiles,
        history_start,
        requested_end,
        adjustment="post",
        refresh_cache=refresh_cache,
    )
    raw_loaded = load_market_histories(
        profiles,
        history_start,
        requested_end,
        adjustment="raw",
        refresh_cache=refresh_cache,
    )
    dividend_histories, dividend_failures = _load_dividend_histories(
        profiles,
        refresh_cache=refresh_cache,
    )
    data = {
        profile.code: CrossBacktestData(
            profile,
            loaded.histories[profile.code],
            raw_loaded.histories[profile.code],
            dividend_histories.get(profile.code, []),
            profile.code in dividend_histories,
        )
        for profile in profiles
        if (
            profile.code in loaded.histories
            and loaded.histories[profile.code]
            and profile.code in raw_loaded.histories
            and raw_loaded.histories[profile.code]
        )
    }
    if not data:
        raise RuntimeError("所选行业没有可用历史行情，请先检查数据源或刷新策略缓存")

    missing_industries = [
        industry_id
        for industry_id in query.industry_ids
        if not any(item.profile.industry_id == industry_id for item in data.values())
    ]
    if missing_industries:
        labels = "、".join(INDUSTRY_CATALOG[item].name for item in missing_industries)
        raise RuntimeError(f"以下已选行业没有任何可用标的：{labels}")

    all_dates = sorted({day for item in data.values() for day in item.market})
    end_date = min(requested_end, all_dates[-1])
    start_date = query.start_date or (end_date - timedelta(days=365 * query.years + 10))
    if start_date > end_date:
        raise RuntimeError("Backtest start_date cannot be later than end_date")
    dates = [day for day in all_dates if start_date <= day <= end_date]
    if len(dates) < MIN_BACKTEST_TRADING_DAYS:
        raise RuntimeError(
            "可用价格历史不足，无法完成跨行业回测："
            f"当前日期区间只有 {len(dates)} 个交易日，至少需要 {MIN_BACKTEST_TRADING_DAYS} 个交易日；"
            f"可用日期范围为 {all_dates[0]} 至 {all_dates[-1]}。"
        )

    results = [
        _run_one_strategy(strategy_id, config, data, dates, query)
        for strategy_id, config in CROSS_STRATEGIES.items()
    ]
    benchmark = _industry_equal_weight_benchmark(data, dates, query.initial_capital)
    primary = next((item for item in results if item.strategy_id == "income_core"), results[0])
    industry_allocation: dict[str, float] = {}
    for holding in primary.current_holdings:
        if holding.industry_id:
            industry_allocation[holding.industry_id] = (
                industry_allocation.get(holding.industry_id, 0.0) + holding.weight
            )
    benchmark_dates = {
        point.date
        for result in results
        for point in result.equity_curve
    }
    return StrategyBacktestResponse(
        module="cross_industry_strategy_backtest",
        title="跨行业高股息与危机防御策略回测",
        start_date=dates[0],
        end_date=dates[-1],
        strategy_count=len(results),
        benchmark_note=(
            "所选行业等权、行业内股票等权的后复权价格基准；后复权序列包含现金分红、"
            "送转与拆并股影响，不重复叠加现金分红。"
        ),
        data_note=(
            "跨行业 v1 使用 Baostock 后复权日线、当日以前的 PB/PE 分位、滚动波动与回撤；"
            "股息率以估值日前最新已实施财年内的现金分红除以当日不复权收盘价；"
            "同一财年内的中期和年度分红合并，避免滚动365日窗口跨财年重复计入，"
            "重复分红事件按除息日和每股派息去重，缺失时标记暂无数据且不使用目录预设值。"
            "业务防御/质量分仍是公开展示的研究先验，不作为历史财务事实。"
            "股票池存在上市存续与幸存者偏差，结果适合验证组合框架，不代表未来收益。"
        ),
        benchmark_curve=_sample_curve(benchmark, required_dates=benchmark_dates),
        selected_industry_ids=list(query.industry_ids),
        universe_size=len(data),
        industry_allocation={key: round(value, 6) for key, value in industry_allocation.items()},
        failures=[*loaded.failures, *raw_loaded.failures, *dividend_failures],
        results=results,
    )


def _run_one_strategy(
    strategy_id: str,
    config: dict[str, str],
    data: dict[str, CrossBacktestData],
    dates: list[date],
    query: CrossIndustryStrategyBacktestQuery,
) -> StrategyBacktestResult:
    portfolio_value = query.initial_capital
    weights: dict[str, float] = {}
    previous_prices: dict[str, float] = {}
    entry_dates: dict[str, date] = {}
    position_cost_bases: dict[str, float] = {}
    position_profits: dict[str, float] = {}
    position_dividend_profits: dict[str, float] = {}
    equity: list[BacktestPoint] = []
    drawdown: list[BacktestPoint] = []
    transaction_cost_curve: list[BacktestPoint] = []
    daily_returns: list[float] = []
    peak = portfolio_value
    total_turnover = 0.0
    total_transaction_cost = 0.0
    estimated_dividend_gain = 0.0
    rebalance_count = 0
    next_rebalance: date | None = None
    latest_candidates: list[CrossCandidate] = []
    holding_snapshots: list[BacktestHoldingSnapshot] = []
    selection_snapshots: list[BacktestSelectionSnapshot] = []

    for index, day in enumerate(dates):
        day_return = 0.0
        value_before_day = portfolio_value
        invested_weight = sum(weights.values())
        asset_returns: dict[str, float] = {}
        for code, weight in weights.items():
            point = data[code].market.get(day)
            if point is None:
                asset_returns[code] = 0.0
                continue
            previous = previous_prices.get(code, point.close)
            asset_return = point.close / previous - 1 if previous > 0 else 0.0
            asset_returns[code] = asset_return
            position_return = weight * asset_return
            day_return += position_return
            previous_prices[code] = point.close
            dividend_cash = _cash_dividend_on_day(data[code].dividends, day)
            raw_previous = _market_price_at_or_before(data[code].raw_market, day - timedelta(days=1))
            if dividend_cash > 0 and raw_previous is not None and raw_previous > 0:
                # Post-adjusted prices already contain the dividend return. This
                # is attribution only and is deliberately not added to day_return.
                dividend_return = dividend_cash / raw_previous
                estimated_dividend_gain += weight * dividend_return
                position_dividend_profits[code] = (
                    position_dividend_profits.get(code, 0.0)
                    + value_before_day * weight * dividend_return
                )
        cash_weight = max(0.0, 1 - invested_weight)
        cash_return = query.cash_yield / 252
        day_return += cash_weight * cash_return
        portfolio_value *= 1 + day_return
        portfolio_factor = max(1 + day_return, 1e-12)
        # Let positions drift naturally between scheduled rebalances. This
        # avoids silently assuming a free daily constant-weight rebalance.
        weights = {
            code: weight * (1 + asset_returns.get(code, 0.0)) / portfolio_factor
            for code, weight in weights.items()
        }
        position_profits = {
            code: portfolio_value * weight - position_cost_bases.get(code, portfolio_value * weight)
            for code, weight in weights.items()
        }
        daily_returns.append(day_return)

        rebalanced = False
        if next_rebalance is None or day >= next_rebalance:
            latest_candidates = _rank_candidates(strategy_id, data, day, query)
            new_weights = _target_weights(strategy_id, latest_candidates, query)
            turnover = sum(
                abs(new_weights.get(code, 0.0) - weights.get(code, 0.0))
                for code in set(new_weights) | set(weights)
            )
            sell_turnover = sum(
                max(weights.get(code, 0.0) - new_weights.get(code, 0.0), 0.0)
                for code in set(new_weights) | set(weights)
            )
            cost = (
                turnover * (query.commission_rate + query.slippage_rate + query.transfer_fee_rate)
                + sell_turnover * query.stamp_duty_rate
            )
            weights_before_rebalance = weights
            value_before_rebalance_cost = portfolio_value
            cost_amount = portfolio_value * cost
            portfolio_value *= max(0.0, 1 - cost)
            total_transaction_cost += cost_amount
            total_turnover += turnover
            position_cost_bases = _rebalance_position_cost_bases(
                position_cost_bases,
                weights_before_rebalance,
                new_weights,
                value_before_rebalance_cost,
                portfolio_value,
            )
            position_dividend_profits = _rebalance_position_dividend_profits(
                position_dividend_profits,
                weights_before_rebalance,
                new_weights,
                value_before_rebalance_cost,
                portfolio_value,
            )
            weights = new_weights
            for code in list(previous_prices):
                if code not in weights:
                    previous_prices.pop(code, None)
            for code in list(entry_dates):
                if code not in weights:
                    entry_dates.pop(code, None)
                    position_profits.pop(code, None)
                    position_dividend_profits.pop(code, None)
            for code in weights:
                if code not in entry_dates:
                    entry_dates[code] = day
                    position_profits[code] = 0.0
                point = data[code].market.get(day)
                if point is not None:
                    previous_prices[code] = point.close
            position_profits = {
                code: portfolio_value * weight - position_cost_bases[code]
                for code, weight in weights.items()
            }
            selection_snapshots.append(
                BacktestSelectionSnapshot(
                    date=day,
                    holdings=_holdings_from_candidates(
                        latest_candidates,
                        weights,
                        entry_dates,
                        position_profits,
                        position_cost_bases,
                        portfolio_value,
                        day,
                        position_dividend_profits,
                    ),
                    candidate_count=len(latest_candidates),
                    cash_weight=round(max(0.0, 1 - sum(weights.values())), 6),
                )
            )
            rebalance_count += 1
            next_rebalance = _advance_rebalance(day, query.rebalance_frequency)
            rebalanced = True

        peak = max(peak, portfolio_value)
        equity.append(BacktestPoint(date=day, value=round(portfolio_value, 6)))
        drawdown.append(BacktestPoint(date=day, value=round(portfolio_value / peak - 1, 6)))
        transaction_cost_curve.append(BacktestPoint(date=day, value=round(total_transaction_cost, 6)))
        holding_snapshots.append(
            BacktestHoldingSnapshot(
                date=day,
                holdings=_holdings_from_candidates(
                    latest_candidates,
                    weights,
                    entry_dates,
                    position_profits,
                    position_cost_bases,
                    portfolio_value,
                    day,
                    position_dividend_profits,
                ),
            )
        )

    metrics = _metrics(
        equity=equity,
        drawdown=drawdown,
        daily_returns=daily_returns,
        cash_yield=query.cash_yield,
        dividend_gain=estimated_dividend_gain,
        total_turnover=total_turnover,
        total_transaction_cost=total_transaction_cost,
        rebalance_count=rebalance_count,
        initial_value=query.initial_capital,
    )
    holdings = _holdings_from_candidates(
        latest_candidates,
        weights,
        entry_dates,
        position_profits,
        position_cost_bases,
        portfolio_value,
        dates[-1],
        position_dividend_profits,
    )
    required_dates = {
        metrics.max_drawdown_date,
        *(holding.entry_date for holding in holdings if holding.entry_date is not None),
    }
    sampled_equity = _sample_curve(equity, required_dates=required_dates)
    sampled_drawdown = _sample_curve(drawdown, required_dates=required_dates)
    sampled_dates = {point.date for point in sampled_equity} | {
        point.date for point in sampled_drawdown
    }
    sampled_snapshots = [
        snapshot for snapshot in holding_snapshots if snapshot.date in sampled_dates
    ]
    holding_price_series = _holding_price_series(
        {code: item.market for code, item in data.items()},
        holdings,
        dates,
    )
    return StrategyBacktestResult(
        strategy_id=strategy_id,  # type: ignore[arg-type]
        strategy_name=config["name"],
        description=config["description"],
        metrics=metrics,
        equity_curve=sampled_equity,
        drawdown_curve=sampled_drawdown,
        transaction_cost_curve=_sample_curve(transaction_cost_curve, required_dates=sampled_dates),
        yearly_returns=_yearly_returns(equity),
        current_holdings=holdings,
        holding_snapshots=sampled_snapshots,
        selection_snapshots=selection_snapshots,
        holding_price_series=holding_price_series,
    )


def _rank_candidates(
    strategy_id: str,
    data: dict[str, CrossBacktestData],
    day: date,
    query: CrossIndustryStrategyBacktestQuery,
) -> list[CrossCandidate]:
    candidates: list[CrossCandidate] = []
    for code, item in data.items():
        point = item.market.get(day)
        if point is None:
            continue
        trailing = [
            (item_day, market_point)
            for item_day, market_point in sorted(item.market.items())
            if item_day <= day
        ]
        if len(trailing) < 60:
            continue
        closes = [market_point.close for _, market_point in trailing[-252:]]
        returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0]
        volatility = statistics.pstdev(returns) * math.sqrt(252) if len(returns) > 1 else .5
        peak = max(closes)
        drawdown = closes[-1] / peak - 1 if peak > 0 else -1
        stability = _clamp(100 - volatility * 150 + drawdown * 35, 0, 100)
        profile = item.profile
        defense = profile.defense_score * .72 + stability * .28
        safety = profile.income_score * .58 + defense * .42
        growth = profile.quality_score * .68 + stability * .32
        risk = _clamp(100 - (defense * .68 + stability * .32), 0, 100)
        valuation_percentile = _valuation_percentile(item, day)
        valuation = (1 - valuation_percentile) * 100
        dividend_yield = _trailing_dividend_yield(item, day)
        if dividend_yield is None and query.min_dividend_yield > 0:
            continue
        dividend_for_score = dividend_yield or 0.0
        if (
            dividend_for_score + 1e-9 < query.min_dividend_yield
            or safety < query.min_dividend_safety
            or growth < query.min_stable_growth
            or risk > query.max_risk_score
        ):
            continue
        dividend_component = min(dividend_for_score / .07, 1) * 100
        total_weight = max(
            query.dividend_weight
            + query.safety_weight
            + query.growth_weight
            + query.valuation_weight
            + query.risk_penalty_weight,
            .01,
        )
        score = (
            dividend_component * query.dividend_weight
            + safety * query.safety_weight
            + growth * query.growth_weight
            + valuation * query.valuation_weight
            - risk * query.risk_penalty_weight
        ) / total_weight
        if strategy_id == "income_core":
            score += safety * .08 + dividend_component * .08
        elif strategy_id == "value_reversion":
            score += valuation * .18 + dividend_component * .04
        else:
            if risk > min(query.max_risk_score, 42):
                continue
            score += defense * .14 + stability * .12 - risk * .1
        candidates.append(
            CrossCandidate(
                code=code,
                name=profile.name,
                industry_id=profile.industry_id,
                score=max(0.0, score),
                dividend_yield=dividend_yield,
                risk_score=risk,
                volatility=volatility,
                dividend_safety_score=safety,
                stable_growth_score=growth,
                quality_score=profile.quality_score,
                valuation_percentile=valuation_percentile,
            )
        )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _valuation_percentile(item: CrossBacktestData, day: date) -> float:
    industry = INDUSTRY_CATALOG[item.profile.industry_id]
    start = day - timedelta(days=365 * 5)
    values: list[float] = []
    current: float | None = None
    for item_day, point in sorted(item.market.items()):
        if item_day > day or item_day < start:
            continue
        value = point.pb if industry.valuation_metric == "pb" else point.pe
        if value is not None and value > 0:
            values.append(value)
            if item_day == day:
                current = value
    if not values or current is None:
        return .5
    return sum(1 for value in values if value <= current) / len(values)


def _target_weights(
    strategy_id: str,
    candidates: list[CrossCandidate],
    query: CrossIndustryStrategyBacktestQuery,
) -> dict[str, float]:
    if not candidates:
        return {}
    by_industry: dict[str, list[CrossCandidate]] = {}
    for candidate in candidates:
        by_industry.setdefault(candidate.industry_id, []).append(candidate)
    budgets = _industry_budgets(by_industry, query)
    if not budgets:
        return {}
    slots = _industry_slots(by_industry, budgets, query.holding_count)
    selected: dict[str, list[CrossCandidate]] = {
        industry_id: by_industry[industry_id][:slots.get(industry_id, 0)]
        for industry_id in budgets
    }
    weights: dict[str, float] = {}
    for industry_id, industry_candidates in selected.items():
        if not industry_candidates:
            continue
        if strategy_id == "income_core":
            raw = [1.0 for _ in industry_candidates]
        elif strategy_id == "value_reversion":
            raw = [max(item.score, 1.0) for item in industry_candidates]
        else:
            raw = [max(item.score, 1.0) / max(item.risk_score + 20, 20) for item in industry_candidates]
        total = sum(raw) or 1.0
        for index, candidate in enumerate(industry_candidates):
            weights[candidate.code] = budgets[industry_id] * raw[index] / total
    return weights


def _industry_budgets(
    by_industry: dict[str, list[CrossCandidate]],
    query: CrossIndustryStrategyBacktestQuery,
) -> dict[str, float]:
    ids = [industry_id for industry_id in query.industry_ids if by_industry.get(industry_id)]
    if not ids:
        return {}
    investable = 1 - query.crisis_cash_buffer
    if len(ids) == 1:
        return {ids[0]: investable}
    if query.industry_weighting == "equal":
        raw = {industry_id: 1.0 for industry_id in ids}
    elif query.industry_weighting == "score":
        raw = {
            industry_id: statistics.fmean(item.score for item in by_industry[industry_id])
            for industry_id in ids
        }
    else:
        raw = {
            industry_id: 1 / max(
                statistics.fmean(item.volatility for item in by_industry[industry_id]), .05
            )
            for industry_id in ids
        }
    return _capped_weights(raw, investable, query.max_industry_weight)


def _capped_weights(raw: dict[str, float], total_weight: float, cap: float) -> dict[str, float]:
    remaining_ids = sorted(raw)
    remaining = total_weight
    output: dict[str, float] = {}
    while remaining_ids and remaining > 1e-12:
        positives = {item: max(raw[item], 0.0) for item in remaining_ids}
        raw_total = sum(positives.values())
        if raw_total <= 0:
            positives = {item: 1.0 for item in remaining_ids}
            raw_total = float(len(remaining_ids))
        proposed = {
            item: remaining * positives[item] / raw_total
            for item in remaining_ids
        }
        capped = [item for item, value in proposed.items() if value > cap + 1e-12]
        if not capped:
            output.update(proposed)
            remaining = 0.0
            break
        for item in capped:
            output[item] = cap
            remaining -= cap
            remaining_ids.remove(item)
    return output


def _industry_slots(
    by_industry: dict[str, list[CrossCandidate]],
    budgets: dict[str, float],
    holding_count: int,
) -> dict[str, int]:
    ids = list(budgets)
    slots = {industry_id: 1 for industry_id in ids}
    remaining = max(0, holding_count - len(ids))
    if remaining == 0:
        return slots
    total_budget = sum(budgets.values()) or 1.0
    quotas = {industry_id: remaining * budgets[industry_id] / total_budget for industry_id in ids}
    for industry_id in ids:
        extra = min(int(math.floor(quotas[industry_id])), max(0, len(by_industry[industry_id]) - 1))
        slots[industry_id] += extra
        remaining -= extra
    order = sorted(
        ids,
        key=lambda item: (quotas[item] - math.floor(quotas[item]), budgets[item], item),
        reverse=True,
    )
    while remaining > 0:
        allocated = False
        for industry_id in order:
            if remaining <= 0:
                break
            if slots[industry_id] < len(by_industry[industry_id]):
                slots[industry_id] += 1
                remaining -= 1
                allocated = True
        if not allocated:
            break
    return slots


def _holdings_from_candidates(
    candidates: list[CrossCandidate],
    weights: dict[str, float],
    entry_dates: dict[str, date],
    position_profits: dict[str, float],
    position_cost_bases: dict[str, float],
    portfolio_value: float,
    day: date,
    position_dividend_profits: dict[str, float],
) -> list[BacktestHolding]:
    return [
        BacktestHolding(
            stock_code=item.code,
            stock_name=item.name,
            industry_id=item.industry_id,
            weight=round(weights[item.code], 6),
            score=round(item.score, 2),
            dividend_yield=round(item.dividend_yield, 6) if item.dividend_yield is not None else None,
            risk_score=round(item.risk_score, 2),
            entry_date=entry_dates.get(item.code),
            holding_days=max(0, (day - entry_dates[item.code]).days) if item.code in entry_dates else 0,
            profit=round(position_profits.get(item.code, 0.0), 2),
            price_profit=round(
                position_profits.get(item.code, 0.0) - position_dividend_profits.get(item.code, 0.0),
                2,
            ),
            dividend_profit=round(position_dividend_profits.get(item.code, 0.0), 2),
            position_value=round(portfolio_value * weights[item.code], 2),
            cost_basis=round(position_cost_bases.get(item.code, 0.0), 2),
            profit_return=round(
                position_profits.get(item.code, 0.0) / position_cost_bases[item.code]
                if position_cost_bases.get(item.code, 0.0) > 0
                else 0.0,
                6,
            ),
            dividend_safety_score=round(item.dividend_safety_score, 2),
            stable_growth_score=round(item.stable_growth_score, 2),
            quality_score=round(item.quality_score, 2),
            valuation_percentile=round(item.valuation_percentile, 6),
        )
        for item in candidates
        if item.code in weights
    ]


def _rebalance_position_cost_bases(
    current_cost_bases: dict[str, float],
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    portfolio_value_before_cost: float,
    portfolio_value_after_cost: float,
) -> dict[str, float]:
    """Carry the remaining average cost through buys and proportional sells."""
    next_cost_bases: dict[str, float] = {}
    for code, target_weight in target_weights.items():
        target_value = max(0.0, portfolio_value_after_cost * target_weight)
        current_value = max(0.0, portfolio_value_before_cost * current_weights.get(code, 0.0))
        current_basis = max(0.0, current_cost_bases.get(code, 0.0))
        if current_value <= 1e-12 or current_basis <= 1e-12:
            next_cost_bases[code] = target_value
        elif target_value >= current_value:
            next_cost_bases[code] = current_basis + target_value - current_value
        else:
            next_cost_bases[code] = current_basis * target_value / current_value
    return next_cost_bases


def _rebalance_position_dividend_profits(
    current_dividend_profits: dict[str, float],
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    portfolio_value_before_cost: float,
    portfolio_value_after_cost: float,
) -> dict[str, float]:
    next_dividend_profits: dict[str, float] = {}
    for code, target_weight in target_weights.items():
        current_value = max(0.0, portfolio_value_before_cost * current_weights.get(code, 0.0))
        target_value = max(0.0, portfolio_value_after_cost * target_weight)
        existing = current_dividend_profits.get(code, 0.0)
        if current_value <= 1e-12:
            next_dividend_profits[code] = 0.0
        elif target_value >= current_value:
            next_dividend_profits[code] = existing
        else:
            next_dividend_profits[code] = existing * target_value / current_value
    return next_dividend_profits


def _load_dividend_histories(
    profiles: list[IndustryStockProfile],
    *,
    refresh_cache: bool,
) -> tuple[dict[str, list[DividendEvent]], list[dict[str, str]]]:
    histories: dict[str, list[DividendEvent]] = {}
    failures: list[dict[str, str]] = []
    for profile in profiles:
        try:
            events = _fetch_dividend_events(profile.code) if refresh_cache else _read_dividend_events(profile.code)
            events = _deduplicate_dividend_events(events)
            if not events:
                failures.append({
                    "stock_code": profile.code,
                    "error": "未取得可核验的已实施现金分红记录，股息率按暂无数据处理",
                })
                continue
            if refresh_cache:
                _write_dividend_events_cache(profile.code, events)
            histories[profile.code] = events
        except Exception as exc:
            failures.append({"stock_code": profile.code, "error": f"股息数据读取失败: {exc}"})
    return histories, failures


def _deduplicate_dividend_events(events: list[DividendEvent]) -> list[DividendEvent]:
    unique: dict[tuple[date, float], DividendEvent] = {}
    for event in events:
        if event.ex_dividend_date is None or event.cash_per_share <= 0:
            continue
        key = (event.ex_dividend_date, round(event.cash_per_share, 8))
        existing = unique.get(key)
        if existing is None or event.announcement_date < existing.announcement_date:
            unique[key] = event
    return sorted(unique.values(), key=lambda event: (event.ex_dividend_date or event.report_date, event.report_date))


def _market_price_at_or_before(
    market: dict[date, SecurityMarketPoint],
    day: date,
) -> float | None:
    available = [item_day for item_day in market if item_day <= day]
    if not available:
        return None
    return market[max(available)].close


def _trailing_dividend_yield(item: CrossBacktestData, day: date) -> float | None:
    if not item.dividend_data_available:
        return None
    price = _market_price_at_or_before(item.raw_market, day)
    if price is None or price <= 0:
        return None
    yearly_cash: dict[int, float] = {}
    for event in _deduplicate_dividend_events(item.dividends):
        if (
            event.ex_dividend_date is not None
            and event.ex_dividend_date <= day
            and event.announcement_date <= day
        ):
            yearly_cash[event.report_date.year] = yearly_cash.get(event.report_date.year, 0.0) + event.cash_per_share
    if not yearly_cash:
        return None
    return yearly_cash[max(yearly_cash)] / price


def _cash_dividend_on_day(events: list[DividendEvent], day: date) -> float:
    return sum(
        event.cash_per_share
        for event in events
        if event.ex_dividend_date == day and event.announcement_date <= day
    )


def _industry_equal_weight_benchmark(
    data: dict[str, CrossBacktestData],
    dates: list[date],
    initial_value: float,
) -> list[BacktestPoint]:
    value = initial_value
    previous_prices: dict[str, float] = {}
    curve: list[BacktestPoint] = []
    for day in dates:
        industry_returns: dict[str, list[float]] = {}
        for code, item in data.items():
            point = item.market.get(day)
            if point is None:
                continue
            previous = previous_prices.get(code)
            if previous is not None and previous > 0:
                industry_returns.setdefault(item.profile.industry_id, []).append(
                    point.close / previous - 1
                )
            previous_prices[code] = point.close
        if industry_returns:
            daily = statistics.fmean(
                statistics.fmean(returns)
                for returns in industry_returns.values()
                if returns
            )
            value *= 1 + daily
        curve.append(BacktestPoint(date=day, value=round(value, 6)))
    return curve


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
