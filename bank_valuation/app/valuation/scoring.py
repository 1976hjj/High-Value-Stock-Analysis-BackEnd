"""Risk flags and non-advisory classification."""
from __future__ import annotations
from ..models import BankInput, RiskAnalysis, RiskDriver


def risk_flags(bank: BankInput, pb_percentile: float) -> list[str]:
    flags: list[str] = []
    if bank.roe < .06: flags.append("ROE低于6%，盈利能力偏弱")
    if bank.roe < bank.long_term_growth: flags.append("ROE低于长期增长率，持续创造价值能力存疑")
    if bank.profit_growth_yoy < -.05: flags.append("净利润同比下降超过5%")
    if bank.payout_ratio > .75 and bank.profit_growth_yoy < 0: flags.append("分红率偏高，未来分红可持续性需关注")
    if bank.npl_ratio_change > 0: flags.append("不良率上升，资产质量承压")
    if bank.provision_coverage_change < 0: flags.append("拨备覆盖率下降，风险缓冲减弱")
    if bank.cet1_ratio is not None and bank.cet1_ratio < .085: flags.append("核心一级资本充足率缓冲不足")
    if pb_percentile < .10 and bank.roe < .06 and bank.profit_growth_yoy < 0:
        flags.append("当前PB处于历史低位，但基本面恶化，存在价值陷阱风险")
    return flags


def final_rating(bank: BankInput, pb_percentile: float, flags: list[str]) -> str:
    deterioration = bank.roe < .06 or bank.profit_growth_yoy < -.05 or (bank.cet1_ratio is not None and bank.cet1_ratio < .085)
    if pb_percentile < .10 and deterioration:
        return "value_trap_risk"
    if (bank.cet1_ratio is not None and bank.cet1_ratio < .075) or (bank.roe < .04 and bank.profit_growth_yoy < -.05):
        return "avoid"
    if pb_percentile < .25 and bank.roe >= .08 and bank.profit_growth_yoy >= 0 and not flags:
        return "deep_value"
    if bank.dividend_yield >= .05 and bank.dividend_stable and not deterioration:
        return "hold_income"
    return "watch"


def risk_analysis(bank: BankInput, pb_percentile: float, flags: list[str]) -> RiskAnalysis:
    """Explain risk transmission paths, not merely whether a numeric rule fired."""
    def state(value: bool, caution: bool = False) -> str:
        return "risk" if value else "watch" if caution else "stable"

    drivers = [
        RiskDriver(
            category="盈利能力", status=state(bank.roe < .06, bank.roe < .08 or bank.profit_growth_yoy < .03),
            current_reading=f"年化ROE {bank.roe:.1%}，净利润同比 {bank.profit_growth_yoy:+.1%}",
            why_it_matters="ROE下降或利润负增长会直接压低可持续盈利和合理PB。",
            deterioration_signal="ROE连续低于8%，或净利润同比转负并持续两个报告期。",
            data_source="Baostock 最新已披露财报",
        ),
        RiskDriver(
            category="净息差与利率环境", status="watch" if bank.nim is None else state(bank.nim_change < -.003, bank.nim_change < -.001),
            current_reading="净息差未获取" if bank.nim is None else f"净息差 {bank.nim:.2%}，变化 {bank.nim_change:+.2%}",
            why_it_matters="贷款重定价快于负债成本下降时，净息差收窄会侵蚀利息收入。",
            deterioration_signal="净息差环比继续收窄超过10bp，且信贷需求走弱。",
            data_source=f"{bank.bank_special_metrics_source}，报告期 {bank.bank_special_metrics_report_date}" if bank.bank_special_metrics_source else "未获取；不使用默认值",
        ),
        RiskDriver(
            category="资产质量", status="watch" if bank.npl_ratio is None else state(bank.npl_ratio > .02 or bank.npl_ratio_change > .002, bank.npl_ratio_change > 0),
            current_reading="不良率未获取" if bank.npl_ratio is None else f"不良率 {bank.npl_ratio:.2%}，变化 {bank.npl_ratio_change:+.2%}",
            why_it_matters="不良生成上升通常会带来减值计提增加，并进一步拖累利润和资本。",
            deterioration_signal="不良率连续上行，关注房地产、地方融资平台及零售贷款的迁徙率。",
            data_source=f"{bank.bank_special_metrics_source}，报告期 {bank.bank_special_metrics_report_date}" if bank.bank_special_metrics_source else "未获取；不使用默认值",
        ),
        RiskDriver(
            category="拨备缓冲", status="watch" if bank.provision_coverage is None else state(bank.provision_coverage < 1.5, bank.provision_coverage_change < 0),
            current_reading="拨备覆盖率未获取（不使用统一默认值）" if bank.provision_coverage is None else f"拨备覆盖率 {bank.provision_coverage:.1%}，变化 {bank.provision_coverage_change:+.1%}",
            why_it_matters="拨备是吸收信用损失的第一道缓冲；缓冲变薄时，未来损失更可能直接冲击利润。",
            deterioration_signal="拨备覆盖率持续下降，并伴随不良率上升。",
            data_source=(f"{bank.provision_coverage_source}，报告期 {bank.provision_coverage_report_date}" if bank.provision_coverage_source else "Baostock 未稳定提供；需接入银行财报、Wind、Choice 或 Tushare 专业口径"),
        ),
        RiskDriver(
            category="资本与分红", status="watch" if bank.cet1_ratio is None else state(bank.cet1_ratio < .085 or (bank.payout_ratio > .75 and bank.profit_growth_yoy < 0), bank.payout_ratio > .65),
            current_reading=f"CET1 {bank.cet1_ratio:.2%}，分红率 {bank.payout_ratio:.1%}" if bank.cet1_ratio is not None else f"CET1未获取，分红率 {bank.payout_ratio:.1%}",
            why_it_matters="资本缓冲不足或高分红叠加利润下滑，可能限制资产扩张并增加分红调整压力。",
            deterioration_signal="CET1接近监管最低要求，或分红率高于75%且盈利下滑。",
            data_source=f"CET1：{bank.bank_special_metrics_source}，报告期 {bank.bank_special_metrics_report_date}；分红率：Baostock 分红与EPS" if bank.bank_special_metrics_source else "CET1未获取；分红率：Baostock 分红与EPS",
        ),
        RiskDriver(
            category="估值重估", status=state(pb_percentile > .90, pb_percentile > .75),
            current_reading=f"当前PB历史分位 {pb_percentile:.1%}",
            why_it_matters="即使基本面稳定，估值处于自身历史高位时，风险偏好回落也可能带来PB回归。",
            deterioration_signal="宏观预期转弱、无风险利率上行或行业盈利预期下修。",
            data_source="Baostock 历史PB序列",
        ),
    ]
    assessment = "当前未触发硬性风险规则，但仍应跟踪下方各项恶化信号。" if not flags else "已有规则触发项，重点关注标记为风险的传导路径。"
    return RiskAnalysis(
        current_assessment=assessment, drivers=drivers,
        market_conditions=[
            "宏观增长和信贷需求走弱，可能压低贷款投放与手续费收入。",
            "政策利率下调节奏、存款竞争和债券收益率变化，会影响净息差与估值折现率。",
            "房地产与地方融资平台相关风险暴露变化，可能影响不良生成及拨备压力。",
            "市场风险偏好变化会影响银行PB，即使单家银行经营指标未立即恶化。",
        ],
    )
