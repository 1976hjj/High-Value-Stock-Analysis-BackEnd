"""Dividend-oriented bank strategy backtests from local cached histories."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import csv
import math
import statistics

from ..data_sources.bank_base import BANK_NAMES, _market_path
from ..models import (
    BacktestHolding,
    BacktestHoldingSnapshot,
    BacktestMetrics,
    BacktestPoint,
    BacktestYearReturn,
    BankInput,
    StrategyBacktestQuery,
    StrategyBacktestResponse,
    StrategyBacktestResult,
)
from .mean_reversion import (
    _dividend_safety_score,
    _load_latest_cached_bank,
    _quality_score,
    _risk_score,
    _stable_growth_score,
)
from .service import value_bank


@dataclass
class MarketPoint:
    close: float
    pb: float


@dataclass
class BankBacktestData:
    bank: BankInput
    market: dict[date, MarketPoint]


@dataclass
class Candidate:
    code: str
    name: str
    score: float
    dividend_yield: float
    risk_score: float


STRATEGIES = {
    "income_core": {
        "name": "高股息稳健持有",
        "description": "优先选股息安全分、稳定增长分和股息率较高且风险分较低的银行。",
        "target_count": 10,
    },
    "value_reversion": {
        "name": "高股息 + 低估修复",
        "description": "在股息有托底的前提下，增加 PB 历史低位和均值回归空间权重。",
        "target_count": 10,
    },
    "defensive_rotation": {
        "name": "股息防守轮动",
        "description": "更严格过滤风险，黄灯降权，候选不足时保留现金仓位。",
        "target_count": 8,
    },
}


def run_strategy_backtest(query: StrategyBacktestQuery) -> StrategyBacktestResponse:
    data = _load_backtest_data()
    if not data:
        raise RuntimeError("本地银行缓存不足；请先运行银行排序或单股分析生成缓存")
    all_dates = sorted({day for item in data.values() for day in item.market})
    end_date = min(query.end_date or all_dates[-1], all_dates[-1])
    start_date = query.start_date or (end_date - timedelta(days=365 * query.years + 10))
    if start_date > end_date:
        raise RuntimeError("Backtest start_date cannot be later than end_date")
    dates = [day for day in all_dates if start_date <= day <= end_date]
    if len(dates) < 252:
        raise RuntimeError("可用价格历史不足，无法完成回测")
    results = [
        _run_one_strategy(strategy_id, config, data, dates, query)
        for strategy_id, config in STRATEGIES.items()
    ]
    benchmark_curve = _bank_equal_weight_benchmark(data, dates, query.initial_capital)
    return StrategyBacktestResponse(
        module="bank_strategy_backtest",
        title="银行高股息策略回测",
        start_date=dates[0],
        end_date=dates[-1],
        strategy_count=len(results),
        benchmark_note="银行股等权价格基准：使用本地银行股日线缓存按每日可用涨跌幅等权滚动，不含股息和交易成本。",
        data_note=(
            "v1 回测使用本地缓存中的日线价格/PB历史，股息收益按当前每股分红折算为日度收益；"
            "财务质量、资产质量和资本风险使用最近已披露快照做过滤。它适合先比较策略框架，"
            "严格无未来函数版本需要补齐历史财报公告日、历史分红和历史监管指标。"
        ),
        benchmark_curve=_sample_curve(benchmark_curve),
        results=results,
    )


def _load_backtest_data() -> dict[str, BankBacktestData]:
    output: dict[str, BankBacktestData] = {}
    requested_date = date.today()
    for code in BANK_NAMES:
        try:
            bank = _load_latest_cached_bank(code, requested_date)
            market = _read_market_history(code)
        except Exception:
            continue
        if len(market) >= 252:
            output[code] = BankBacktestData(bank=bank, market=market)
    return output


def _read_market_history(code: str) -> dict[date, MarketPoint]:
    path = _market_path(code)
    if not path.exists():
        return {}
    output: dict[date, MarketPoint] = {}
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            try:
                output[date.fromisoformat(row["date"])] = MarketPoint(
                    close=float(row["close"]),
                    pb=float(row["pb"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
    return output


def _bank_equal_weight_benchmark(
    data: dict[str, BankBacktestData],
    dates: list[date],
    initial_value: float,
) -> list[BacktestPoint]:
    value = initial_value
    previous_prices: dict[str, float] = {}
    curve: list[BacktestPoint] = []

    for day in dates:
        daily_returns: list[float] = []
        for code, item in data.items():
            point = item.market.get(day)
            if point is None:
                continue
            previous = previous_prices.get(code)
            if previous is not None and previous > 0:
                daily_returns.append(point.close / previous - 1)
            previous_prices[code] = point.close
        if daily_returns:
            value *= 1 + sum(daily_returns) / len(daily_returns)
        curve.append(BacktestPoint(date=day, value=round(value, 6)))

    return curve


def _run_one_strategy(
    strategy_id: str,
    config: dict[str, object],
    data: dict[str, BankBacktestData],
    dates: list[date],
    query: StrategyBacktestQuery,
) -> StrategyBacktestResult:
    portfolio_value = query.initial_capital
    weights: dict[str, float] = {}
    previous_prices: dict[str, float] = {}
    equity: list[BacktestPoint] = []
    drawdown: list[BacktestPoint] = []
    transaction_cost_curve: list[BacktestPoint] = []
    daily_returns: list[float] = []
    peak = portfolio_value
    total_turnover = 0.0
    total_transaction_cost = 0.0
    dividend_gain = 0.0
    rebalance_count = 0
    next_rebalance: date | None = None
    latest_candidates: list[Candidate] = []
    holding_snapshots: list[BacktestHoldingSnapshot] = []

    for day in dates:
        day_return = 0.0
        day_dividend = 0.0
        invested_weight = sum(weights.values())
        for code, weight in weights.items():
            point = data[code].market.get(day)
            if point is None:
                continue
            previous = previous_prices.get(code, point.close)
            if previous > 0:
                day_return += weight * (point.close / previous - 1)
            dividend_yield = _historical_dividend_yield(data[code].bank, point.close)
            dividend_part = weight * dividend_yield / 252
            day_return += dividend_part
            day_dividend += dividend_part
            previous_prices[code] = point.close
        cash_weight = max(0.0, 1 - invested_weight)
        day_return += cash_weight * query.cash_yield / 252
        portfolio_value *= 1 + day_return
        dividend_gain += day_dividend
        daily_returns.append(day_return)

        if next_rebalance is None or day >= next_rebalance:
            latest_candidates = _rank_candidates(strategy_id, data, day, query)
            new_weights = _target_weights(strategy_id, latest_candidates, query.holding_count)
            turnover = sum(abs(new_weights.get(code, 0.0) - weights.get(code, 0.0)) for code in set(new_weights) | set(weights))
            sell_turnover = sum(max(weights.get(code, 0.0) - new_weights.get(code, 0.0), 0.0) for code in set(new_weights) | set(weights))
            cost = (
                turnover * (query.commission_rate + query.slippage_rate + query.transfer_fee_rate)
                + sell_turnover * query.stamp_duty_rate
            )
            cost_amount = portfolio_value * cost
            portfolio_value *= max(0.0, 1 - cost)
            total_transaction_cost += cost_amount
            total_turnover += turnover
            weights = new_weights
            for code in list(previous_prices):
                if code not in weights:
                    del previous_prices[code]
            for code in weights:
                point = data[code].market.get(day)
                if point is not None:
                    previous_prices[code] = point.close
            holding_snapshots.append(
                BacktestHoldingSnapshot(
                    date=day,
                    holdings=_holdings_from_candidates(latest_candidates, weights),
                )
            )
            rebalance_count += 1
            next_rebalance = _advance_rebalance(day, query.rebalance_frequency)

        peak = max(peak, portfolio_value)
        equity.append(BacktestPoint(date=day, value=round(portfolio_value, 6)))
        drawdown.append(BacktestPoint(date=day, value=round(portfolio_value / peak - 1, 6)))
        transaction_cost_curve.append(BacktestPoint(date=day, value=round(total_transaction_cost, 6)))

    metrics = _metrics(
        equity=equity,
        drawdown=drawdown,
        daily_returns=daily_returns,
        cash_yield=query.cash_yield,
        dividend_gain=dividend_gain,
        total_turnover=total_turnover,
        total_transaction_cost=total_transaction_cost,
        rebalance_count=rebalance_count,
    )
    yearly = _yearly_returns(equity)
    holdings = _holdings_from_candidates(latest_candidates, weights)
    return StrategyBacktestResult(
        strategy_id=strategy_id,  # type: ignore[arg-type]
        strategy_name=str(config["name"]),
        description=str(config["description"]),
        metrics=metrics,
        equity_curve=_sample_curve(equity),
        drawdown_curve=_sample_curve(drawdown, required_dates={metrics.max_drawdown_date}),
        transaction_cost_curve=_sample_curve(transaction_cost_curve),
        yearly_returns=yearly,
        current_holdings=holdings,
        holding_snapshots=holding_snapshots,
    )


def _holdings_from_candidates(candidates: list[Candidate], weights: dict[str, float]) -> list[BacktestHolding]:
    return [
        BacktestHolding(
            stock_code=item.code,
            stock_name=item.name,
            weight=round(weights.get(item.code, 0.0), 6),
            score=round(item.score, 2),
            dividend_yield=round(item.dividend_yield, 6),
            risk_score=round(item.risk_score, 2),
        )
        for item in candidates
        if weights.get(item.code, 0.0) > 0
    ]


def _rank_candidates(
    strategy_id: str,
    data: dict[str, BankBacktestData],
    day: date,
    query: StrategyBacktestQuery,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for code, item in data.items():
        point = item.market.get(day)
        if point is None:
            continue
        bank = item.bank
        risk = _risk_score(bank, "watch", [])
        safety = _dividend_safety_score(bank, risk)
        stable = _stable_growth_score(bank, risk)
        quality = _quality_score(bank)
        dividend_yield = _historical_dividend_yield(bank, point.close)
        pb_percentile = _pb_percentile(item.market, day, point.pb, years=5)
        median_pb = _median_pb_until(item.market, day, years=5)
        reversion = max(0.0, median_pb / point.pb - 1) if point.pb > 0 and median_pb > 0 else 0.0

        if (
            risk > query.max_risk_score
            or safety < query.min_dividend_safety
            or stable < query.min_stable_growth
            or dividend_yield < query.min_dividend_yield
            or bank.payout_ratio > query.max_payout_ratio
        ):
            continue

        dividend_component = min(dividend_yield / .08, 1) * 100
        valuation_component = (1 - pb_percentile) * 55 + min(reversion / .5, 1) * 45
        total_weight = max(
            query.dividend_weight
            + query.safety_weight
            + query.growth_weight
            + query.valuation_weight
            + query.risk_penalty_weight,
            .01,
        )
        base_score = (
            dividend_component * query.dividend_weight
            + safety * query.safety_weight
            + stable * query.growth_weight
            + valuation_component * query.valuation_weight
            - risk * query.risk_penalty_weight
        ) / total_weight

        if strategy_id == "income_core":
            if risk >= query.max_risk_score * .95:
                continue
            score = base_score + safety * .08 + stable * .06 + dividend_component * .08
        elif strategy_id == "value_reversion":
            if reversion <= 0 and pb_percentile > .55:
                continue
            score = base_score + valuation_component * .18 + dividend_component * .04 - risk * .04
        else:
            if risk > min(query.max_risk_score, 36) or bank.profit_growth_yoy < -.08:
                continue
            score = base_score + safety * .14 + stable * .12 + quality * .08 - risk * .12
        candidates.append(Candidate(code=code, name=BANK_NAMES.get(code, bank.stock_name), score=max(0.0, score), dividend_yield=dividend_yield, risk_score=risk))
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def _target_weights(strategy_id: str, candidates: list[Candidate], target_count: int) -> dict[str, float]:
    selected = candidates[:target_count]
    if not selected:
        return {}
    if strategy_id == "defensive_rotation":
        invested = min(1.0, len(selected) / target_count)
        raw = [max(item.score, 1.0) / max(item.risk_score + 20, 20) for item in selected]
    elif strategy_id == "value_reversion":
        invested = 1.0
        raw = [max(item.score, 1.0) for item in selected]
    else:
        invested = 1.0
        raw = [1.0 for _ in selected]
    total = sum(raw) or 1.0
    return {item.code: invested * raw[index] / total for index, item in enumerate(selected)}


def _historical_dividend_yield(bank: BankInput, price: float) -> float:
    if price <= 0 or bank.dividend_per_share <= 0:
        return 0.0
    return min(bank.dividend_per_share / price, 0.15)


def _pb_percentile(market: dict[date, MarketPoint], day: date, current_pb: float, years: int) -> float:
    start = day - timedelta(days=365 * years)
    values = [point.pb for item_day, point in market.items() if start <= item_day <= day and point.pb > 0]
    if not values:
        return .5
    below = sum(1 for value in values if value <= current_pb)
    return below / len(values)


def _median_pb_until(market: dict[date, MarketPoint], day: date, years: int) -> float:
    start = day - timedelta(days=365 * years)
    values = [point.pb for item_day, point in market.items() if start <= item_day <= day and point.pb > 0]
    return statistics.median(values) if values else 0.0


def _advance_rebalance(day: date, frequency: str) -> date:
    months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}[frequency]
    month = day.month + months
    year = day.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    return date(year, month, 1)


def _metrics(
    *,
    equity: list[BacktestPoint],
    drawdown: list[BacktestPoint],
    daily_returns: list[float],
    cash_yield: float,
    dividend_gain: float,
    total_turnover: float,
    total_transaction_cost: float,
    rebalance_count: int,
) -> BacktestMetrics:
    start_value = equity[0].value
    end_value = equity[-1].value
    days = max((equity[-1].date - equity[0].date).days, 1)
    total_return = end_value / start_value - 1
    annualized = (end_value / start_value) ** (365 / days) - 1
    max_drawdown_index, max_drawdown_point = min(enumerate(drawdown), key=lambda item: item[1].value)
    max_drawdown = max_drawdown_point.value
    previous_peak = max(equity[: max_drawdown_index + 1], key=lambda point: point.value)
    recovery_point = next(
        (point for point in equity[max_drawdown_index + 1:] if point.value >= previous_peak.value),
        None,
    )
    recovery_days = (
        (recovery_point.date - max_drawdown_point.date).days
        if recovery_point is not None
        else None
    )
    volatility = statistics.pstdev(daily_returns) * math.sqrt(252) if len(daily_returns) > 1 else 0.0
    sharpe = None if volatility == 0 else (annualized - cash_yield) / volatility
    calmar = None if max_drawdown == 0 else annualized / abs(max_drawdown)
    yearly = _yearly_returns(equity)
    win_year_rate = sum(1 for item in yearly if item.return_rate > 0) / len(yearly) if yearly else 0.0
    annual_dividend = dividend_gain / max(days / 365, 1)
    return BacktestMetrics(
        total_return=round(total_return, 6),
        annualized_return=round(annualized, 6),
        max_drawdown=round(max_drawdown, 6),
        max_drawdown_date=max_drawdown_point.date,
        recovery_date=recovery_point.date if recovery_point is not None else None,
        recovery_days=recovery_days,
        volatility=round(volatility, 6),
        sharpe=round(sharpe, 4) if sharpe is not None else None,
        calmar=round(calmar, 4) if calmar is not None else None,
        win_year_rate=round(win_year_rate, 6),
        annual_dividend_return=round(annual_dividend, 6),
        turnover=round(total_turnover, 6),
        rebalance_count=rebalance_count,
        total_transaction_cost=round(total_transaction_cost, 2),
    )


def _yearly_returns(equity: list[BacktestPoint]) -> list[BacktestYearReturn]:
    by_year: dict[int, list[BacktestPoint]] = {}
    for point in equity:
        by_year.setdefault(point.date.year, []).append(point)
    output = []
    for year, points in sorted(by_year.items()):
        if len(points) < 2:
            continue
        output.append(BacktestYearReturn(year=year, return_rate=round(points[-1].value / points[0].value - 1, 6)))
    return output


def _sample_curve(
    points: list[BacktestPoint],
    max_points: int = 220,
    required_dates: set[date] | None = None,
) -> list[BacktestPoint]:
    if len(points) <= max_points:
        return points
    step = max(1, len(points) // max_points)
    sampled = points[::step]
    if required_dates:
        existing = {point.date for point in sampled}
        sampled.extend(point for point in points if point.date in required_dates and point.date not in existing)
        sampled.sort(key=lambda point: point.date)
    if sampled[-1].date != points[-1].date:
        sampled.append(points[-1])
    return sampled
