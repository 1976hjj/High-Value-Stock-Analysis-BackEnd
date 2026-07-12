"""Transparent price-scenario projection for automated non-bank industries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import (
    IndustryPanorama,
    IndustryPriceProjection,
    IndustryPriceScenario,
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _raw(panorama: IndustryPanorama, key: str, fallback: float = 0.0) -> float:
    for group in panorama.groups:
        for metric in group.metrics:
            if metric.key == key and metric.raw_value is not None:
                return metric.raw_value
    return fallback


def _pct(value: float) -> str:
    return f"{value:.1%}"


@dataclass(frozen=True)
class _ScenarioSpec:
    scenario_id: str
    name: str
    earnings_change: float
    pe_factor: float
    target_yield: float
    dividend_weight: float
    uncertainty: float
    confidence_adjustment: float
    triggers: tuple[str, ...]


def build_price_projection(
    industry_id: str,
    panorama: IndustryPanorama,
    *,
    current_price: float,
    current_pe: float | None,
    market_date: date,
    quality_score: float,
    valuation_percentile: float,
    stock_code: str | None = None,
) -> IndustryPriceProjection | None:
    if industry_id not in {"hydro", "consumer", "resources", "oilgas", "tollroad", "nuclear", "telecom"} or current_pe is None or current_pe <= 0:
        return None
    implied_eps = current_price / current_pe
    dividend_per_share = _raw(panorama, "dividend_cash_ttm")
    cash_conversion = _raw(panorama, "cfo_to_np", 1.0)
    liability_ratio = _raw(panorama, "liability_ratio", .45)

    if industry_id == "hydro":
        revenue_growth = _raw(panorama, "revenue_growth_proxy")
        profit_growth = _raw(panorama, "profit_growth_proxy")
        trend = _clamp(revenue_growth * .55 + profit_growth * .20, -.08, .12)
        base_pe = _clamp(
            current_pe
            * _clamp(1 + (.50 - valuation_percentile) * .25, .85, 1.15)
            * _clamp(1 + (quality_score - 70) / 100 * .08, .94, 1.06),
            8,
            30,
        )
        specs = (
            _ScenarioSpec("bull", "丰水与电价友好", _clamp(trend + .10, -.02, .25), 1.12, .030, .20, .07, .02, ("来水或发电量高于多年均值", "平均电价稳定", "现金转换不恶化")),
            _ScenarioSpec("base", "正常水文基准", trend, 1.00, .040, .35, .07, 0, ("来水回归多年均值", "收入与利润趋势可持续", "负债率不继续抬升")),
            _ScenarioSpec("bear", "枯水与估值收缩", _clamp(trend - .18, -.35, -.08), .82, .055, .45, .10, -.08, ("连续枯水或电量下降", "电价折让扩大", "利息保障下降")),
            _ScenarioSpec("crisis", "政策或极端枯水", -.35 if liability_ratio < .65 else -.42, .65, .070, .55, .12, -.15, ("电价规则不利变化", "极端枯水跨年度延续", "融资成本和资本开支同时上升")),
        )
        model_name = "水电 EPS-PE + 实际股息锚情景模型"
        industry_drivers = [
            f"收入景气代理 {_pct(revenue_growth)}",
            f"盈利景气代理 {_pct(profit_growth)}",
            f"资产负债率 {_pct(liability_ratio)}",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，再施加情景盈利变化",
            "目标PE由当前PE、五年估值分位和自动质量分共同调整",
            "实际近12月现金分红按不同目标股息率形成第二估值锚",
            "来水没有统一结构化实测源，收入和利润增速仅作为财务代理",
        ]
    elif industry_id == "resources":
        revenue_growth = _raw(panorama, "commodity_revenue_proxy")
        profit_growth = _raw(panorama, "commodity_profit_proxy")
        gross_margin = _raw(panorama, "gross_margin")
        asset_growth = _raw(panorama, "asset_growth")
        # Resource earnings are mean-reverting. Only a limited share of reported
        # growth is carried into the base case; leverage and aggressive expansion
        # reduce the normalized multiple.
        cycle_trend = _clamp(revenue_growth * .25 + profit_growth * .12, -.12, .12)
        balance_penalty = _clamp((liability_ratio - .45) * .30 + max(0, asset_growth - .12) * .35, 0, .18)
        base_pe = _clamp(
            current_pe
            * _clamp(1 + (.50 - valuation_percentile) * .18, .86, 1.12)
            * _clamp(1 + (quality_score - 70) / 100 * .06 - balance_penalty, .78, 1.08),
            5,
            22,
        )
        specs = (
            _ScenarioSpec("bull", "供给约束与商品上行", _clamp(cycle_trend + .22, .08, .38), 1.08, .045, .24, .11, .01, ("收入与利润代理同步上行", "毛利率扩张且现金转换稳定", "公司未在景气高位激进扩产")),
            _ScenarioSpec("base", "中周期价格基准", cycle_trend, 1.00, .060, .32, .12, 0, ("盈利向中周期水平收敛", "经营现金流覆盖基础分红", "资产负债率和扩张速度可控")),
            _ScenarioSpec("bear", "需求收缩与去库存", _clamp(cycle_trend - .30, -.48, -.16), .78, .085, .38, .16, -.10, ("收入和利润代理转负", "毛利率与库存周转同时恶化", "高股息因现金流回落而下修")),
            _ScenarioSpec("crisis", "政策、事故或价格崩塌", -.52 if liability_ratio < .60 else -.62, .60, .110, .42, .20, -.18, ("商品价格快速跌破行业现金成本", "安全环保或地缘事件导致停产", "资本开支和偿债压力同时上升")),
        )
        model_name = "资源中周期 EPS-PE + 可持续股息锚情景模型"
        industry_drivers = [
            f"商品景气收入代理 {_pct(revenue_growth)}",
            f"商品景气利润代理 {_pct(profit_growth)}",
            f"毛利率 {_pct(gross_margin)}",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
            f"资产负债率 {_pct(liability_ratio)}",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，但只保留部分当期增速，避免把周期峰值利润永久化",
            "目标PE由当前PE、五年估值分位、质量分、负债率和高位扩张风险共同调整",
            "实际近12月现金分红按更高目标股息率折现，检验高股息在周期下行时是否仍有支撑",
            "煤价、铜价、金价和单位现金成本暂无统一结构化源，收入、利润和毛利率均明确标为财务代理",
        ]
    elif industry_id == "oilgas":
        revenue_growth = _raw(panorama, "oil_revenue_proxy")
        profit_growth = _raw(panorama, "oil_profit_proxy")
        gross_margin = _raw(panorama, "gross_margin")
        asset_growth = _raw(panorama, "asset_growth")
        inventory_days = _raw(panorama, "inventory_days", 50)
        # CNOOC is upstream-heavy, PetroChina is integrated, Sinopec has the
        # largest downstream buffer. This changes earnings sensitivity, not the
        # observed financial inputs.
        upstream_weight = {"600938": .90, "601857": .62, "600028": .38}.get(stock_code or "", .65)
        downstream_buffer = 1 - upstream_weight
        cycle_trend = _clamp(
            (revenue_growth * .26 + profit_growth * .14) * (.72 + upstream_weight * .28),
            -.12,
            .12,
        )
        capital_penalty = _clamp(
            max(0, liability_ratio - .48) * .28 + max(0, asset_growth - .12) * .30,
            0,
            .16,
        )
        base_pe = _clamp(
            current_pe
            * _clamp(1 + (.50 - valuation_percentile) * .20, .86, 1.12)
            * _clamp(1 + (quality_score - 70) / 100 * .07 - capital_penalty, .80, 1.09),
            5,
            24,
        )
        specs = (
            _ScenarioSpec("bull", "供给冲击与高油价", _clamp(cycle_trend + .26 * upstream_weight + .08 * downstream_buffer, .08, .36), 1.08, .045, .22, .10, .01, ("油气景气收入与利润代理同步改善", "毛利率扩张且现金转换稳定", "资本开支没有随高油价失控")),
            _ScenarioSpec("base", "中周期油价与一体化平衡", cycle_trend, 1.00, .060, .30, .11, 0, ("盈利向中周期油价收敛", "炼化销售对上游波动形成部分缓冲", "经营现金覆盖基础分红和必要投资")),
            _ScenarioSpec("bear", "需求衰退与油价下行", _clamp(cycle_trend - .34 * upstream_weight - .16 * downstream_buffer, -.48, -.16), .76, .085, .36, .15, -.10, ("收入利润代理同步转负", "库存天数上升或综合毛利率下降", "自由现金流不足以覆盖分红与资本开支")),
            _ScenarioSpec("crisis", "油价崩塌或转型重估", -.50 if liability_ratio < .58 else -.60, .58, .110, .40, .19, -.18, ("油价长期跌破高成本项目盈亏线", "碳成本、减值或地缘停产冲击", "负债、资本开支与分红压力同时上升")),
        )
        model_name = "油气中周期 EPS-PE + 一体化缓冲 + 可持续股息锚模型"
        industry_drivers = [
            f"上游敏感权重 {upstream_weight:.0%}",
            f"炼化销售缓冲 {downstream_buffer:.0%}",
            f"油气景气收入代理 {_pct(revenue_growth)}",
            f"油气景气利润代理 {_pct(profit_growth)}",
            f"综合毛利率 {_pct(gross_margin)}",
            f"库存周转 {inventory_days:.1f}天",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，但仅保留部分当期增速，避免把高油价利润永久化",
            "中国海油、中国石油和中国石化按上游占比差异设置盈利敏感度与炼化销售缓冲",
            "目标PE由五年估值分位、质量分、负债率和高油价时期扩张风险共同调整",
            "实际近12月分红按压力目标股息率形成第二估值锚，检验基础分红可持续性",
            "Brent油价、桶油成本和储量替代率暂无统一结构化源，财务指标均明确标为代理",
        ]
    elif industry_id == "tollroad":
        revenue_growth = _raw(panorama, "traffic_revenue_proxy")
        profit_growth = _raw(panorama, "traffic_profit_proxy")
        asset_growth = _raw(panorama, "asset_growth")
        interest_cover = _raw(panorama, "interest_cover", 4.0)
        # Explicit research-prior duration factors. They are conservative
        # relative adjustments, not claimed remaining concession years.
        duration_factor = {
            "600377": 1.00,
            "600350": .94,
            "001965": .98,
            "600012": .90,
            "600548": .88,
        }.get(stock_code or "", .92)
        traffic_trend = _clamp(revenue_growth * .48 + profit_growth * .18, -.08, .10)
        debt_penalty = _clamp(max(0, liability_ratio - .48) * .24 + max(0, asset_growth - .12) * .28, 0, .15)
        base_pe = _clamp(
            current_pe
            * duration_factor
            * _clamp(1 + (.50 - valuation_percentile) * .20, .88, 1.12)
            * _clamp(1 + (quality_score - 70) / 100 * .07 - debt_penalty, .82, 1.08),
            7,
            25,
        )
        specs = (
            _ScenarioSpec("bull", "车流复苏与期限改善", _clamp(traffic_trend + .10, .04, .20), 1.08, .040, .32, .07, .02, ("收入和利润代理同步改善", "经营现金覆盖分红且债务下降", "收费政策或补偿机制明确")),
            _ScenarioSpec("base", "成熟车流与有限收费权", traffic_trend, 1.00, .050, .44, .08, 0, ("车流随名义经济温和增长", "费率与免费政策保持稳定", "新增投资不侵蚀成熟路产现金")),
            _ScenarioSpec("bear", "车流走弱与分流", _clamp(traffic_trend - .16, -.28, -.08), .82, .070, .52, .11, -.08, ("收入代理转负或货运需求走弱", "分流道路开通或收费优惠扩大", "利息保障和分红覆盖下降")),
            _ScenarioSpec("crisis", "收费期限或费率政策冲击", -.32 if liability_ratio < .58 else -.40, .66, .090, .60, .15, -.16, ("收费期限缩短且补偿机制不清", "费率下调或免费天数显著增加", "高杠杆收购与现金流下降同时发生")),
        )
        model_name = "收费公路有限期限 EPS-PE + 可持续股息锚情景模型"
        industry_drivers = [
            f"车流费率收入代理 {_pct(revenue_growth)}",
            f"车流盈利代理 {_pct(profit_growth)}",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
            f"资产负债率 {_pct(liability_ratio)}",
            f"利息保障 {interest_cover:.2f}×",
            f"收费权期限研究系数 {duration_factor:.2f}",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，再用车流与盈利代理形成情景EPS",
            "收费权不是永续资产，目标PE乘以显式期限研究系数并接受政策冲击折价",
            "实际近12月现金分红按压力目标股息率形成重要估值锚，现金转换和债务决定其可信度",
            "逐条路产剩余收费年限、同口径车流和费率暂无统一结构化源，必须用年报及经营公告复核",
            "高资产增速和高负债会被视为低回报扩张风险，而不是自动增加价值",
        ]
    elif industry_id == "nuclear":
        revenue_growth = _raw(panorama, "nuclear_generation_proxy")
        profit_growth = _raw(panorama, "nuclear_profit_proxy")
        asset_growth = _raw(panorama, "asset_growth")
        interest_cover = _raw(panorama, "interest_cover", 3.0)
        asset_turnover = _raw(panorama, "asset_turnover", .16)
        pipeline_factor = {"601985": 1.04, "003816": .98}.get(stock_code or "", 1.0)
        generation_trend = _clamp(revenue_growth * .48 + profit_growth * .20, -.08, .12)
        leverage_penalty = _clamp(max(0, liability_ratio - .65) * .35 + max(0, 2.5 - interest_cover) * .025, 0, .18)
        execution_adjustment = _clamp((asset_growth - .06) * .18 + (asset_turnover - .16) * .12, -.05, .05)
        base_pe = _clamp(
            current_pe
            * pipeline_factor
            * _clamp(1 + (.50 - valuation_percentile) * .24, .85, 1.14)
            * _clamp(1 + (quality_score - 70) / 100 * .08 + execution_adjustment - leverage_penalty, .78, 1.10),
            9,
            32,
        )
        specs = (
            _ScenarioSpec("bull", "新机组投产与电价友好", _clamp(generation_trend + .12, .04, .24), 1.10, .025, .18, .08, .02, ("新机组按期商运并贡献电量", "利用小时与市场化电价稳定", "经营现金增速快于债务增长")),
            _ScenarioSpec("base", "稳定运行与有序投产", generation_trend, 1.00, .035, .25, .09, 0, ("在运机组保持安全稳定", "新项目按计划投产", "现金流覆盖利息、必要投资和基础分红")),
            _ScenarioSpec("bear", "电价承压与投产延迟", _clamp(generation_trend - .18, -.32, -.08), .80, .050, .32, .13, -.10, ("市场化电价折让扩大", "大修或投产延迟拖累电量", "利息保障下降且负债继续上升")),
            _ScenarioSpec("crisis", "安全事件或建设重估", -.38 if liability_ratio < .72 else -.48, .60, .070, .38, .18, -.20, ("重大安全监管事件导致停机或审批收紧", "在建项目延期并发生减值", "融资成本、资本开支和分红压力同时上升")),
        )
        model_name = "核电投产周期 EPS-PE + 债务现金闸门 + 股息锚模型"
        industry_drivers = [
            f"核电量价收入代理 {_pct(revenue_growth)}",
            f"机组盈利代理 {_pct(profit_growth)}",
            f"资产增长/在建代理 {_pct(asset_growth)}",
            f"资产周转 {asset_turnover:.2f}×",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
            f"资产负债率 {_pct(liability_ratio)}",
            f"利息保障 {interest_cover:.2f}×",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，再由核电量价和利润代理形成情景EPS",
            "中国核电偏成长投产、中国广核偏成熟运营，使用不同项目管线研究系数",
            "目标PE由五年估值分位、质量分、资产增长、周转效率、负债率和利息保障共同调整",
            "实际分红是辅助锚；高资本开支阶段必须先验证经营现金对利息和必要投资的覆盖",
            "实际利用小时、市场化电价、机组投产进度和核安全事件需用经营公告复核",
        ]
    elif industry_id == "telecom":
        revenue_growth = _raw(panorama, "telecom_revenue_proxy")
        profit_growth = _raw(panorama, "telecom_profit_proxy")
        asset_growth = _raw(panorama, "asset_growth")
        receivable_days = _raw(panorama, "receivable_days", 45)
        asset_turnover = _raw(panorama, "asset_turnover", .45)
        operator_factor = {"600941": 1.05, "601728": 1.01, "600050": .96}.get(stock_code or "", 1.0)
        service_trend = _clamp(revenue_growth * .52 + profit_growth * .24, -.05, .10)
        capex_penalty = _clamp(max(0, asset_growth - .08) * .35 + max(0, receivable_days - 75) / 500, 0, .13)
        cash_bonus = _clamp((cash_conversion - 2.2) * .025 + (asset_turnover - .40) * .08, -.04, .06)
        base_pe = _clamp(
            current_pe
            * operator_factor
            * _clamp(1 + (.50 - valuation_percentile) * .24, .86, 1.14)
            * _clamp(1 + (quality_score - 70) / 100 * .08 + cash_bonus - capex_penalty, .82, 1.10),
            7,
            24,
        )
        specs = (
            _ScenarioSpec("bull", "ARPU稳定与资本开支释放", _clamp(service_trend + .08, .03, .18), 1.08, .040, .32, .07, .02, ("收入利润代理同步改善", "网络资产增速放缓且现金转换稳定", "派息承诺由现金而非杠杆支持")),
            _ScenarioSpec("base", "通信主业稳定与云网增长", service_trend, 1.00, .050, .42, .08, 0, ("ARPU与用户结构大致稳定", "产业数字化增长未显著拉长回款", "资本开支维持正常化趋势")),
            _ScenarioSpec("bear", "资费竞争与新一轮投资", _clamp(service_trend - .14, -.25, -.06), .82, .070, .50, .11, -.08, ("收入代理放缓且利润弱于收入", "资产增速重新抬升或应收账期拉长", "经营现金对派息的缓冲下降")),
            _ScenarioSpec("crisis", "政策或技术替代冲击", -.28 if liability_ratio < .55 else -.36, .66, .090, .58, .15, -.16, ("资费监管或强制投资显著升级", "重大技术替代削弱现有网络回报", "派息承诺、现金流和资产负债表同时承压")),
        )
        model_name = "电信订阅现金流 EPS-PE + 资本开支闸门 + 可持续股息锚模型"
        industry_drivers = [
            f"用户与ARPU收入代理 {_pct(revenue_growth)}",
            f"运营利润代理 {_pct(profit_growth)}",
            f"网络投资/资产增长代理 {_pct(asset_growth)}",
            f"资产周转 {asset_turnover:.2f}×",
            f"应收周转 {receivable_days:.1f}天",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，再用用户ARPU与利润代理形成情景EPS",
            "中国移动偏现金流核心、中国电信偏云网成长、中国联通偏改革弹性，使用不同运营商质量系数",
            "资产增长与应收账期作为资本开支和第二曲线现金质量代理，高扩张会压低目标PE",
            "实际近12月分红是重要估值锚，但经营现金流尚未扣资本开支，不能直接等同自由现金流",
            "移动ARPU、用户净增、云业务收入和资本开支需用月度经营数据及年报分部披露复核",
        ]
    else:
        revenue_growth = _raw(panorama, "revenue_growth")
        profit_growth = _raw(panorama, "profit_growth")
        gross_margin = _raw(panorama, "gross_margin")
        trend = _clamp(revenue_growth * .50 + profit_growth * .30, -.10, .15)
        base_pe = _clamp(
            current_pe
            * _clamp(1 + (.50 - valuation_percentile) * .30, .82, 1.18)
            * _clamp(1 + (quality_score - 70) / 100 * .10, .93, 1.08),
            10,
            45,
        )
        specs = (
            _ScenarioSpec("bull", "需求与品牌共振", _clamp(trend + .10, 0, .28), 1.15, .025, .10, .08, .02, ("收入和利润增速同步改善", "毛利率稳定或上升", "现金转换接近或高于1倍")),
            _ScenarioSpec("base", "稳健复购基准", trend, 1.00, .035, .15, .09, 0, ("收入保持当前趋势", "利润现金含量稳定", "渠道营运资本未恶化")),
            _ScenarioSpec("bear", "需求降级与折扣", _clamp(trend - .20, -.35, -.08), .75, .050, .20, .12, -.10, ("收入转负或利润显著慢于收入", "毛利率下降", "存货与应收占用上升")),
            _ScenarioSpec("crisis", "品牌或渠道危机", -.38, .55, .065, .25, .15, -.18, ("品牌信任或产品质量事件", "渠道退货和折扣快速上升", "经营现金流明显弱于利润")),
        )
        model_name = "消费 EPS-PE + 现金分红交叉情景模型"
        industry_drivers = [
            f"收入同比 {_pct(revenue_growth)}",
            f"利润同比 {_pct(profit_growth)}",
            f"毛利率 {_pct(gross_margin)}",
            f"经营现金流/净利润 {cash_conversion:.2f}×",
        ]
        assumptions = [
            "以当前价/PE反推EPS TTM，再施加情景盈利变化",
            "目标PE由当前PE、五年估值分位和自动质量分共同调整",
            "实际近12月现金分红只作为辅助锚，主要价值仍来自盈利与现金流",
            "终端动销和经销商库存没有统一结构化源，使用收入、利润和营运资本代理",
        ]

    confidence_base = _clamp(.55 + panorama.coverage_ratio * .30, .55, .85)
    scenarios: list[IndustryPriceScenario] = []
    for spec in specs:
        target_pe = _clamp(base_pe * spec.pe_factor, 5, 60)
        scenario_eps = max(.01, implied_eps * (1 + spec.earnings_change))
        earnings_price = scenario_eps * target_pe
        dividend_price = dividend_per_share / spec.target_yield if dividend_per_share > 0 else earnings_price
        price_mid = earnings_price * (1 - spec.dividend_weight) + dividend_price * spec.dividend_weight
        price_low = max(.01, price_mid * (1 - spec.uncertainty))
        price_high = price_mid * (1 + spec.uncertainty)
        scenarios.append(
            IndustryPriceScenario(
                id=spec.scenario_id,
                name=spec.name,
                earnings_change=round(spec.earnings_change, 6),
                target_pe=round(target_pe, 4),
                dividend_yield_anchor=spec.target_yield if dividend_per_share > 0 else None,
                price_low=round(price_low, 2),
                price_mid=round(price_mid, 2),
                price_high=round(price_high, 2),
                return_low=round(price_low / current_price - 1, 6),
                return_mid=round(price_mid / current_price - 1, 6),
                return_high=round(price_high / current_price - 1, 6),
                confidence=round(_clamp(confidence_base + spec.confidence_adjustment, .35, .9), 4),
                drivers=industry_drivers,
                triggers=list(spec.triggers),
                formula="情景价 = 情景EPS×目标PE×盈利权重 + 实际每股分红÷目标股息率×股息权重",
            )
        )
    base = next(item for item in scenarios if item.id == "base")
    bear = next(item for item in scenarios if item.id == "bear")
    base_return = base.return_mid
    if base_return >= .15 and bear.return_mid >= -.30:
        conclusion = "基准价值高于现价且悲观情景仍有一定缓冲，可进入买点与风险复核。"
    elif base_return <= -.10:
        conclusion = "基准情景价值低于现价，当前价格已透支部分基本面，优先等待。"
    else:
        conclusion = "基准情景接近现价，预期回报主要取决于后续盈利兑现，暂不形成强价格信号。"
    defensive_entry = max(.01, min(base.price_low * .90, bear.price_high))
    return IndustryPriceProjection(
        model_name=model_name,
        current_price=round(current_price, 2),
        current_pe=round(current_pe, 4),
        implied_eps_ttm=round(implied_eps, 4),
        market_date=market_date,
        report_date=panorama.report_date,
        scenarios=scenarios,
        base_value_mid=base.price_mid,
        defensive_entry_price=round(defensive_entry, 2),
        conclusion=conclusion,
        assumptions=assumptions,
        data_note="价格区间是基于点时财报和行情的情景估值，不是未来股价保证；区间会随收盘价、财报和分红更新。",
    )
