"""Scenario templates and dynamic probability allocation."""
from __future__ import annotations
from copy import deepcopy
from ..models import BankInput

DEFAULT_SCENARIOS = {
    "bull": {"name": "bull", "roe_range": (0.11, 0.13), "profit_growth_range": (0.03, 0.06), "target_pb_range": (0.90, 1.30), "trigger_conditions": ["ROE回升至11%以上", "盈利增长恢复", "估值修复"], "macro_conditions": ["信贷需求回暖，企业与零售贷款投放改善", "稳增长政策降低尾部信用风险", "利率与存款竞争趋于稳定，息差压力缓和"], "bank_conditions": ["ROE维持或回升至11%以上", "净利润同比持续增长3%以上", "不良率稳定或下降，拨备缓冲充足"], "valuation_trigger": "行业风险偏好回升，市场愿意给予优质银行更高PB。"},
    "base": {"name": "base", "roe_range": (0.08, 0.10), "profit_growth_range": (0.00, 0.03), "target_pb_range": (0.60, 0.90), "trigger_conditions": ["盈利平稳", "资产质量稳定", "分红维持"], "macro_conditions": ["经济温和增长，信贷需求没有明显超预期", "政策利率保持宽松但存款竞争仍存在", "风险偏好中性，银行估值缺少显著催化"], "bank_conditions": ["ROE保持8%至10%", "利润增速接近零至低个位数", "资产质量与资本缓冲总体稳定，分红延续"], "valuation_trigger": "市场维持常态化折价，不发生明显估值扩张或压缩。"},
    "bear": {"name": "bear", "roe_range": (0.05, 0.07), "profit_growth_range": (-0.05, 0.00), "target_pb_range": (0.35, 0.60), "trigger_conditions": ["息差承压", "盈利下滑", "资产质量走弱"], "macro_conditions": ["有效信贷需求偏弱，贷款定价下行", "存款成本下降慢于资产收益率，息差被压缩", "房地产、地方融资平台或消费信用风险抬升"], "bank_conditions": ["ROE下滑至5%至7%", "利润同比转负或接近零", "不良率上行且拨备覆盖率下降"], "valuation_trigger": "行业盈利预期下修，投资者提高风险折价并压低目标PB。"},
    "crisis": {"name": "crisis", "roe_range": (0.00, 0.04), "profit_growth_range": (-0.15, -0.05), "target_pb_range": (0.15, 0.35), "trigger_conditions": ["显著不良暴露", "资本缓冲收窄", "需大幅重估"], "macro_conditions": ["经济或金融市场发生显著冲击，信用风险集中暴露", "流动性紧张或无风险利率上行加剧估值压力", "政策应对不足以迅速稳定资产质量预期"], "bank_conditions": ["ROE跌至4%以下，利润同比下降超过5%", "不良率显著上升，拨备难以覆盖新增损失", "核心一级资本缓冲收窄，可能需要削减分红或补充资本"], "valuation_trigger": "市场优先定价资本与资产质量风险，PB进入危机折价区间。"},
}


def scenario_template() -> dict:
    return deepcopy(DEFAULT_SCENARIOS)


def scenario_probabilities(bank: BankInput, pb_percentile: float) -> dict[str, float]:
    weights = {"bull": 0.20, "base": 0.50, "bear": 0.22, "crisis": 0.08}
    low_pb = pb_percentile < 0.10
    healthy = bank.roe > 0.08 and bank.profit_growth_yoy >= 0 and bank.dividend_stable
    weak = bank.roe < 0.06 and bank.profit_growth_yoy < 0
    if low_pb and healthy:
        weights["bull"] += 0.12; weights["base"] += 0.08; weights["bear"] -= 0.12; weights["crisis"] -= 0.08
    if low_pb and weak:
        weights["bear"] += 0.14; weights["crisis"] += 0.12; weights["bull"] -= 0.10; weights["base"] -= 0.16
    if bank.profit_growth_yoy < -0.05:
        weights["bear"] += 0.08; weights["crisis"] += 0.05; weights["base"] -= 0.08; weights["bull"] -= 0.05
    if bank.npl_ratio_change > 0 and bank.provision_coverage_change < 0:
        weights["crisis"] += 0.12; weights["bear"] += 0.05; weights["base"] -= 0.10; weights["bull"] -= 0.07
    if bank.cet1_ratio is not None and bank.cet1_ratio < 0.085:
        weights["crisis"] += 0.10; weights["base"] -= 0.07; weights["bull"] -= 0.03
    if bank.nim_change < -0.002:
        weights["bear"] += 0.05; weights["base"] -= 0.03; weights["bull"] -= 0.02
    weights = {key: max(0.01, value) for key, value in weights.items()}
    total = sum(weights.values())
    # Last member absorbs float rounding, making the sum exactly 1 for API users.
    result = {key: round(value / total, 6) for key, value in weights.items()}
    result["crisis"] = round(1 - sum(result[key] for key in ("bull", "base", "bear")), 6)
    return result


def scenario_prices(bank: BankInput, probabilities: dict[str, float]) -> dict[str, dict]:
    scenarios = scenario_template()
    for name, config in scenarios.items():
        low, high = config["target_pb_range"]
        config["price_range"] = (round(bank.bps * low, 4), round(bank.bps * high, 4))
        config["scenario_probability"] = probabilities[name]
        config["current_fit"] = _current_fit(name, bank)
    return scenarios


def _current_fit(name: str, bank: BankInput) -> dict[str, list[str] | str]:
    """Explain how the current snapshot relates to each possible scenario."""
    if name == "bull":
        supporting = []
        gaps = []
        (supporting if bank.roe >= .10 else gaps).append("ROE已达到10%以上" if bank.roe >= .10 else "ROE需进一步升至11%以上")
        (supporting if bank.profit_growth_yoy >= .03 else gaps).append("利润增速已在3%以上" if bank.profit_growth_yoy >= .03 else "利润增速需升至3%以上")
        (supporting if bank.npl_ratio_change <= 0 else gaps).append("资产质量未见恶化" if bank.npl_ratio_change <= 0 else "需扭转不良率上行")
    elif name == "base":
        supporting = ["盈利和资产质量未出现剧烈变化"]
        gaps = ["需继续观察息差与信贷需求是否稳定"]
    elif name == "bear":
        supporting = []
        gaps = []
        (supporting if bank.profit_growth_yoy < 0 else gaps).append("利润已转为负增长" if bank.profit_growth_yoy < 0 else "若利润转负，悲观情景概率将上升")
        (supporting if bank.nim_change < -.001 else gaps).append("净息差已有明显收窄" if bank.nim_change < -.001 else "若净息差收窄超过10bp，需提高关注")
        (supporting if bank.npl_ratio_change > 0 else gaps).append("不良率正在上行" if bank.npl_ratio_change > 0 else "若不良率上行，将提高该情景概率")
    else:
        supporting = []
        gaps = []
        (supporting if bank.cet1_ratio < .085 else gaps).append("资本缓冲偏紧" if bank.cet1_ratio < .085 else "当前CET1仍高于8.5%缓冲线")
        (supporting if bank.roe < .04 else gaps).append("ROE已跌至危机区间" if bank.roe < .04 else "需警惕ROE快速降至4%以下")
        (supporting if bank.npl_ratio_change > 0 and bank.provision_coverage_change < 0 else gaps).append("资产质量与拨备同时恶化" if bank.npl_ratio_change > 0 and bank.provision_coverage_change < 0 else "需警惕不良上升叠加拨备下降")
    return {"assessment": "当前已有部分信号" if supporting else "当前尚未满足核心条件", "supporting_signals": supporting, "gaps_or_watchpoints": gaps}
