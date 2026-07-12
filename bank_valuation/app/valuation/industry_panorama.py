"""Build automated, industry-specific metric panoramas from reported data."""
from __future__ import annotations

from collections.abc import Iterable

from ..data_sources.financial_snapshot import FinancialSnapshot
from ..models import IndustryPanorama, IndustryPanoramaGroup, IndustryPanoramaMetric


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return min(maximum, max(minimum, value))


def _higher(value: float, bad: float, good: float) -> float:
    if good == bad:
        return 65.0
    return _clamp(30 + (value - bad) / (good - bad) * 60)


def _lower(value: float, good: float, bad: float) -> float:
    return 100 - _higher(value, good, bad) + 20


def _status(score: float) -> str:
    if score >= 82:
        return "strong"
    if score >= 65:
        return "stable"
    if score >= 45:
        return "watch"
    return "risk"


def _pct(value: float) -> str:
    return f"{value:.1%}"


def _ratio(value: float) -> str:
    return f"{value:.2f}×"


def _days(value: float) -> str:
    return f"{value:.1f} 天"


def _money_per_share(value: float) -> str:
    return f"¥{value:.3f}/股"


def _hundred_million(value: float) -> str:
    return f"{value / 100_000_000:.2f} 亿元"


def _metric(
    key: str,
    label: str,
    raw_value: float | None,
    display: str,
    score: float,
    quality: str,
    interpretation: str,
    source: str,
) -> IndustryPanoramaMetric | None:
    if raw_value is None:
        return None
    bounded = round(_clamp(score), 2)
    return IndustryPanoramaMetric(
        key=key,
        label=label,
        value=display,
        raw_value=round(raw_value, 8),
        score=bounded,
        status=_status(bounded),
        quality=quality,
        interpretation=interpretation,
        source=source,
    )


def _group(group_id: str, title: str, metrics: Iterable[IndustryPanoramaMetric | None]) -> IndustryPanoramaGroup:
    return IndustryPanoramaGroup(id=group_id, title=title, metrics=[metric for metric in metrics if metric is not None])


def _market_metrics(
    values: dict[str, float | None],
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaMetric | None]:
    total_share = values.get("totalShare")
    cfo = values.get("cfo_annualized")
    market_cap = current_price * total_share if total_share is not None else None
    cfo_yield = cfo / market_cap if cfo is not None and market_cap and market_cap > 0 else None
    dividend_yield = snapshot.cash_dividend_ttm / current_price if current_price > 0 else None
    earnings_yield = 1 / current_pe if current_pe is not None and current_pe > 0 else None
    market_source = "Baostock 点时行情 + 财务快照计算"
    return [
        _metric("dividend_cash_ttm", "近12月每股现金分红", snapshot.cash_dividend_ttm, _money_per_share(snapshot.cash_dividend_ttm), _higher(snapshot.cash_dividend_ttm, 0, max(snapshot.cash_dividend_ttm, .5)), "reported", "按估值日前实际实施日期汇总", "Baostock 分红实施记录"),
        _metric("dividend_yield", "近12月股息率", dividend_yield, _pct(dividend_yield or 0), _higher(dividend_yield or 0, .015, .05), "derived", "以实际实施现金分红除以当前价格", market_source),
        _metric("pe_ttm", "PE TTM", current_pe, _ratio(current_pe or 0), _lower(current_pe or 0, 12, 35), "reported", "倍数越高，对未来增长和折现率越敏感", "Baostock 日线估值"),
        _metric("pb_mrq", "PB MRQ", current_pb, _ratio(current_pb or 0), _lower(current_pb or 0, 1.2, 6), "reported", "需与 ROE 和资产结构共同解释", "Baostock 日线估值"),
        _metric("valuation_percentile", "近五年估值分位", valuation_percentile, _pct(valuation_percentile), _lower(valuation_percentile, .25, .85), "derived", "分位越高，估值安全垫越薄", "Baostock 五年交易日分位"),
        _metric("earnings_yield", "盈利收益率", earnings_yield, _pct(earnings_yield or 0), _higher(earnings_yield or 0, .025, .075), "derived", "PE 的倒数，用于与资金成本比较", market_source),
        _metric("cfo_yield_proxy", "经营现金流收益率代理", cfo_yield, _pct(cfo_yield or 0), _higher(cfo_yield or 0, .02, .08), "proxy", "使用年化经营现金流代理，尚未扣除资本开支", market_source),
        _metric("volatility", "近一年年化波动", volatility, _pct(volatility), _lower(volatility, .15, .45), "derived", "衡量价格路径风险，不代替基本面风险", "Baostock 后复权日线"),
        _metric("drawdown", "近一年峰值回撤", drawdown, _pct(drawdown), _higher(drawdown, -.4, -.08), "derived", "越接近零代表近期回撤越小", "Baostock 后复权日线"),
    ]


def _hydro_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    return [
        _group("asset", "水文与资产代理", [
            _metric("profit_growth_proxy", "来水/电量盈利代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.15, .15), "proxy", "净利润同比受来水、电量、电价共同驱动，需结合经营公告复核", statement),
            _metric("revenue_growth_proxy", "发电收入景气代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.1, .12), "proxy", "营业收入同比用于代理量价合计变化", statement),
            _metric("noncurrent_asset_ratio", "非流动资产占比", v.get("NCAToAsset"), _pct(v.get("NCAToAsset") or 0), _higher(v.get("NCAToAsset") or 0, .55, .9), "reported", "反映水电站等长寿命资产在总资产中的占比", statement),
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _higher(v.get("YOYAsset") or 0, -.08, .08), "reported", "用于识别扩张、投产或资产收缩", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .03, .2), "reported", "水电属重资产行业，应与自身历史和同业比较", statement),
        ]),
        _group("operation", "盈利与运营结果", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.1, .12), "derived", "当期收入；季报口径为报告期累计值", statement),
            _metric("gross_margin", "毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .25, .65), "reported", "反映电价、来水和资产效率的综合结果", statement),
            _metric("net_margin", "净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .1, .4), "reported", "扣除折旧、利息和税后的盈利转换", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 20, 100), "reported", "电费回收变慢会占用经营现金", statement),
            _metric("inventory_days", "存货周转天数", v.get("INVTurnDays"), _days(v.get("INVTurnDays") or 0), _lower(v.get("INVTurnDays") or 0, 5, 60), "reported", "水电存货通常较低，异常抬升需核对业务混合", statement),
        ]),
        _group("finance", "财务与现金流", [
            _metric("roe_annualized", "年化 ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .06, .15), "derived", "将当年累计 ROE 按季度年化，季节性较强时需谨慎", statement),
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, .7, 1.3), "reported", "检验利润现金含量与分红基础", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .15, .55), "reported", "衡量每元收入转化的经营现金", statement),
            _metric("interest_cover", "EBIT 利息保障", v.get("ebitToInterest"), _ratio(v.get("ebitToInterest") or 0), _higher(v.get("ebitToInterest") or 0, 1.5, 5), "reported", "枯水情景下仍应覆盖利息支出", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .4, .75), "reported", "杠杆越高，来水与利率冲击越容易放大", statement),
            _metric("cash_ratio", "现金比率", v.get("cashRatio"), _ratio(v.get("cashRatio") or 0), _higher(v.get("cashRatio") or 0, .03, .25), "reported", "短期现金对流动负债的覆盖", statement),
        ]),
        _group("return", "股东回报与估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _consumer_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    return [
        _group("brand", "品牌与需求代理", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.08, .15), "derived", "收入由净利润与净利率反算（原字段缺失时），用于量价总结果观察", statement),
            _metric("revenue_growth", "营业收入同比", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.08, .15), "derived", "真实需求仍需用终端动销验证", statement),
            _metric("profit_growth", "归母净利润同比", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.12, .18), "reported", "与收入增速对照可识别利润率扩张或收缩", statement),
            _metric("gross_margin", "毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .2, .65), "reported", "品牌定价、结构升级与成本共同作用的结果", statement),
            _metric("net_margin", "净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .05, .25), "reported", "衡量品牌优势最终转化为利润的效率", statement),
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _higher(v.get("YOYAsset") or 0, -.08, .12), "reported", "结合收入增长判断扩张是否有效", statement),
        ]),
        _group("channel", "渠道与营运资本", [
            _metric("inventory_days", "存货周转天数", v.get("INVTurnDays"), _days(v.get("INVTurnDays") or 0), _lower(v.get("INVTurnDays") or 0, 35, 300), "reported", "公司存货而非经销商库存；白酒等长周期品类需与自身历史比较", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 20, 100), "reported", "账期拉长可能意味着渠道议价或压货风险", statement),
            _metric("inventory_turnover", "存货周转率", v.get("INVTurnRatio"), _ratio(v.get("INVTurnRatio") or 0), _higher(v.get("INVTurnRatio") or 0, .8, 8), "reported", "必须按品类解释，不能横比白酒和家电", statement),
            _metric("receivable_turnover", "应收账款周转率", v.get("NRTurnRatio"), _ratio(v.get("NRTurnRatio") or 0), _higher(v.get("NRTurnRatio") or 0, 3, 20), "reported", "回款速度越快，渠道现金质量通常越高", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .5, 3), "reported", "观察库存、应收与现金共同占用效率", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .05, .25), "reported", "比单看合同负债更直接地检验回款", statement),
        ]),
        _group("finance", "财务与资本回报", [
            _metric("roe_annualized", "年化 ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .1, .25), "derived", "高 ROE 需同时核对杠杆与现金转换", statement),
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, .7, 1.2), "reported", "连续接近或高于 1 表示利润现金含量较好", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .25, 1.2), "reported", "与净利率共同构成经营资本效率", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .2, .7), "reported", "低杠杆为品牌投资与危机应对保留空间", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .6, 1.5), "reported", "不依赖存货变现的短期偿债能力", statement),
            _metric("eps_ttm", "EPS TTM", v.get("epsTTM"), f"¥{(v.get('epsTTM') or 0):.3f}", _higher(profit_growth or 0, -.12, .18), "reported", "用于与股价和历史盈利能力对照", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(v.get("CFOToNP") or 0, .7, 1.2), "derived", "按报告季度年化，季节性强的公司需谨慎", statement),
        ]),
        _group("value", "股东回报与估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _resources_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    """Resource-cycle panorama using reported financials as commodity/cost proxies."""
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    gross_margin = v.get("gpMargin")
    cash_conversion = v.get("CFOToNP")
    liability_ratio = v.get("liabilityToAsset")
    return [
        _group("cycle", "商品周期与盈利弹性", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.2, .2), "derived", "收入规模用于观察产量与商品价格共同形成的结果", statement),
            _metric("commodity_revenue_proxy", "商品景气收入代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.2, .2), "proxy", "收入同比代理实现价格、销量与产品结构的合计变化，不冒充煤价或金属现货价", statement),
            _metric("commodity_profit_proxy", "商品景气利润代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.35, .35), "proxy", "利润同比体现商品价格与经营杠杆，需结合公司产销量公告复核", statement),
            _metric("operating_leverage_proxy", "周期经营杠杆代理", (profit_growth / revenue_growth) if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01 else None, _ratio((profit_growth / revenue_growth) if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01 else 0), _lower(abs((profit_growth / revenue_growth) if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01 else 0), 1, 4), "proxy", "利润增速除以收入增速；绝对值过高意味着价格变化可能放大盈利波动", statement),
            _metric("gross_margin", "毛利率", gross_margin, _pct(gross_margin or 0), _higher(gross_margin or 0, .12, .45), "reported", "在商品价格回落时，毛利率韧性可代理成本曲线位置", statement),
            _metric("net_margin", "净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .04, .25), "reported", "反映资源禀赋、成本与税费最终转化为利润的能力", statement),
        ]),
        _group("cost", "成本曲线与运营效率代理", [
            _metric("inventory_days", "存货周转天数", v.get("INVTurnDays"), _days(v.get("INVTurnDays") or 0), _lower(v.get("INVTurnDays") or 0, 20, 180), "reported", "库存上升可能意味着需求转弱、在产品增加或价格下跌风险", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 20, 100), "reported", "回款变慢会放大周期下行时的现金占用", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .15, .8), "reported", "重资产资源公司的产能利用与销售效率代理", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .05, .3), "reported", "衡量每元资源收入真正转化为经营现金的比例", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .3, 2), "reported", "识别存货和应收是否吞噬商品景气带来的现金", statement),
        ]),
        _group("balance", "资产负债表与资本纪律", [
            _metric("cfo_to_np", "经营现金流/净利润", cash_conversion, _ratio(cash_conversion or 0), _higher(cash_conversion or 0, .65, 1.25), "reported", "周期高点利润只有转成现金才可支持分红和逆周期投资", statement),
            _metric("liability_ratio", "资产负债率", liability_ratio, _pct(liability_ratio or 0), _lower(liability_ratio or 0, .3, .7), "reported", "低杠杆提高商品价格下跌时的生存能力", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .5, 1.5), "reported", "不依赖出售存货的短期偿债能力", statement),
            _metric("roe_annualized", "年化 ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .06, .22), "derived", "必须与商品周期位置共同判断，峰值 ROE 不宜直接外推", statement),
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _lower(v.get("YOYAsset") or 0, .02, .3), "reported", "景气高位资产快速扩张通常意味着资本纪律风险", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(cash_conversion or 0, .65, 1.25), "derived", "按报告季度年化，资源品季节性与价格波动会造成偏差", statement),
        ]),
        _group("return", "股东回报与周期估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _oilgas_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    """Integrated oil and gas panorama from reported financial statements."""
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    operating_leverage = (
        profit_growth / revenue_growth
        if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01
        else None
    )
    return [
        _group("upstream", "油价与上游盈利代理", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.18, .18), "derived", "油气价格、销量与炼化销售共同形成的收入结果", statement),
            _metric("oil_revenue_proxy", "油气景气收入代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.18, .18), "proxy", "收入同比代理实现油价、油气销量和下游产品价格的合计变化，不冒充Brent现货价", statement),
            _metric("oil_profit_proxy", "上游盈利弹性代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.30, .30), "proxy", "利润同比反映油价、桶油成本、产量和炼化价差的综合影响", statement),
            _metric("oil_operating_leverage", "油气经营杠杆代理", operating_leverage, _ratio(operating_leverage or 0), _lower(abs(operating_leverage or 0), 1, 4), "proxy", "利润增速除以收入增速；波动过大意味着油价变化会显著放大盈利", statement),
            _metric("gross_margin", "综合毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .12, .35), "reported", "上游实现价格、桶油成本和炼化销售价差的综合结果", statement),
            _metric("net_margin", "综合净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .025, .15), "reported", "检验一体化结构最终转化为净利润的效率", statement),
        ]),
        _group("integration", "炼化销售与运营缓冲代理", [
            _metric("inventory_days", "存货周转天数", v.get("INVTurnDays"), _days(v.get("INVTurnDays") or 0), _lower(v.get("INVTurnDays") or 0, 25, 100), "reported", "原油与成品油库存既占用现金，也会产生库存收益或损失", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 20, 80), "reported", "销售回款变慢会削弱下游网络的现金缓冲", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .25, 1.1), "reported", "炼化销售型公司的周转效率通常高于纯上游，需按业务结构解释", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .04, .20), "reported", "衡量油气收入真正转化为经营现金的比例", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .8, 4), "reported", "观察库存、应收和现金共同占用效率", statement),
        ]),
        _group("capital", "资本开支、杠杆与资源续航代理", [
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, .75, 1.45), "reported", "高油价利润只有转成现金才可覆盖资本开支和分红", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .32, .68), "reported", "低杠杆提高低油价和能源转型冲击下的韧性", statement),
            _metric("interest_cover", "EBIT利息保障", v.get("ebitToInterest"), _ratio(v.get("ebitToInterest") or 0), _higher(v.get("ebitToInterest") or 0, 2, 8), "reported", "低油价情景仍需覆盖利息和必要资本支出", statement),
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _lower(v.get("YOYAsset") or 0, .03, .22), "reported", "高油价时期快速扩张可能降低下一周期资本回报", statement),
            _metric("roe_annualized", "年化ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .06, .20), "derived", "必须结合油价周期位置，不能直接外推峰值ROE", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .45, 1.2), "reported", "不依赖出售原油和成品油库存的短期偿债能力", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(v.get("CFOToNP") or 0, .75, 1.45), "derived", "按报告季度年化，油价与季节性会造成偏差", statement),
        ]),
        _group("return", "股东回报与中周期估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _tollroad_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    """Mature toll-road panorama using reported traffic/cash-flow proxies."""
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    operating_leverage = profit_growth / revenue_growth if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01 else None
    return [
        _group("traffic", "车流、费率与通行收入代理", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.10, .12), "derived", "通行费、配套业务和并表范围共同形成的收入结果", statement),
            _metric("traffic_revenue_proxy", "同口径车流与费率代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.10, .12), "proxy", "收入同比代理车流、车型结构、费率和并表变化，不冒充实际车流量", statement),
            _metric("traffic_profit_proxy", "车流盈利弹性代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.15, .15), "proxy", "利润同比体现车流、成本刚性、财务费用和投资收益的综合影响", statement),
            _metric("traffic_operating_leverage", "通行收入经营杠杆代理", operating_leverage, _ratio(operating_leverage or 0), _lower(abs(operating_leverage or 0), 1, 3.5), "proxy", "利润增速除以收入增速；过高表示固定成本会放大车流变化", statement),
            _metric("gross_margin", "综合毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .25, .65), "reported", "路产收费、养护成本和非路业务混合后的盈利能力", statement),
            _metric("net_margin", "综合净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .12, .45), "reported", "扣除折旧、利息和投资损益后的股东利润转换", statement),
        ]),
        _group("asset", "路产运营与期限消耗代理", [
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .12, .45), "reported", "重资产路网每单位资产形成收入的效率", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .25, .65), "reported", "收费现金回款较快，该比率异常下降需核对非路业务和营运资本", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 15, 80), "reported", "收费结算或非路业务回款变慢会削弱现金可见性", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .6, 3), "reported", "观察现金、应收和其他流动资产使用效率", statement),
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _lower(v.get("YOYAsset") or 0, .02, .20), "reported", "成熟路产快速扩张需核对新增项目回报和收费期限", statement),
        ]),
        _group("finance", "债务、现金与分红承载", [
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, .85, 1.45), "reported", "检验会计利润是否形成可偿债和可分红现金", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .30, .65), "reported", "剩余收费期限越短，越需要更快降低债务", statement),
            _metric("interest_cover", "EBIT利息保障", v.get("ebitToInterest"), _ratio(v.get("ebitToInterest") or 0), _higher(v.get("ebitToInterest") or 0, 2, 7), "reported", "车流下行情景仍应覆盖利息和必要养护", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .35, 1.2), "reported", "短期现金资产对流动负债的覆盖", statement),
            _metric("roe_annualized", "年化ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .07, .16), "derived", "需结合收费权剩余期限与杠杆判断是否可持续", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(v.get("CFOToNP") or 0, .85, 1.45), "derived", "按报告季度年化，节假日和车流季节性会造成偏差", statement),
        ]),
        _group("return", "有限收费权与股东回报", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _nuclear_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    """Nuclear operator panorama from reported generation/capex proxies."""
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    operating_leverage = profit_growth / revenue_growth if profit_growth is not None and revenue_growth is not None and abs(revenue_growth) >= .01 else None
    return [
        _group("generation", "在运机组、利用小时与电价代理", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.08, .15), "derived", "在运容量、利用小时、上网电价及其他业务共同形成的收入", statement),
            _metric("nuclear_generation_proxy", "核电量价收入代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.08, .15), "proxy", "收入同比代理核电上网电量、电价与新机组并表，不冒充实际利用小时", statement),
            _metric("nuclear_profit_proxy", "机组盈利变化代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.12, .18), "proxy", "利润同比体现电量、电价、折旧、利息和少数股东损益", statement),
            _metric("nuclear_operating_leverage", "核电经营杠杆代理", operating_leverage, _ratio(operating_leverage or 0), _lower(abs(operating_leverage or 0), 1, 3.5), "proxy", "利润增速除以收入增速，用于识别固定成本与利息的放大效应", statement),
            _metric("gross_margin", "综合毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .30, .55), "reported", "利用小时、电价、燃料和折旧共同作用的结果", statement),
            _metric("net_margin", "综合净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .10, .30), "reported", "扣除折旧、财务费用和税后的盈利转换", statement),
        ]),
        _group("construction", "在建机组与投产节奏代理", [
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _higher(v.get("YOYAsset") or 0, .02, .16), "proxy", "资产增长代理在建机组投入和新项目扩张，需用项目清单复核", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .10, .28), "reported", "在建资产占比高时周转率会下降，新机组投产后应逐步改善", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .28, .60), "reported", "衡量成熟在运机组每元收入形成的经营现金", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 35, 140), "reported", "电费和补贴回收变慢会占用建设期资金", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .5, 2), "reported", "观察现金、应收与核燃料等流动资产使用效率", statement),
        ]),
        _group("finance", "资本开支、债务与现金覆盖", [
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, 1.0, 2.0), "reported", "核电折旧较高，经营现金通常应明显高于净利润", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .48, .78), "reported", "建设期高杠杆常见，但必须与投产节奏和现金覆盖匹配", statement),
            _metric("interest_cover", "EBIT利息保障", v.get("ebitToInterest"), _ratio(v.get("ebitToInterest") or 0), _higher(v.get("ebitToInterest") or 0, 1.8, 5.5), "reported", "电价下行或投产延迟时仍需覆盖利息支出", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .35, 1.0), "reported", "建设高峰期短期流动性缓冲", statement),
            _metric("roe_annualized", "年化ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .06, .14), "derived", "需结合杠杆、投产进度和少数股东权益解释", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(v.get("CFOToNP") or 0, 1.0, 2.0), "derived", "按报告季度年化，换料大修和投产时点会造成季节偏差", statement),
        ]),
        _group("return", "股东回报、长久期与估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def _telecom_groups(
    snapshot: FinancialSnapshot,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> list[IndustryPanoramaGroup]:
    """Telecom operator panorama from reported subscriber/capex proxies."""
    v = snapshot.values
    statement = f"Baostock {snapshot.report_date.isoformat()} 财务报告"
    revenue_growth = v.get("revenue_yoy")
    profit_growth = v.get("YOYNI") or v.get("net_profit_yoy_calculated")
    return [
        _group("subscriber", "用户、ARPU与通信主业代理", [
            _metric("revenue", "报告期营业收入", v.get("revenue"), _hundred_million(v.get("revenue") or 0), _higher(revenue_growth or 0, -.04, .10), "derived", "用户规模、ARPU、云和产业数字化共同形成的收入", statement),
            _metric("telecom_revenue_proxy", "用户与ARPU收入代理", revenue_growth, _pct(revenue_growth or 0), _higher(revenue_growth or 0, -.04, .10), "proxy", "收入同比代理用户、ARPU与业务结构变化，不冒充实际移动ARPU", statement),
            _metric("telecom_profit_proxy", "运营利润变化代理", profit_growth, _pct(profit_growth or 0), _higher(profit_growth or 0, -.06, .12), "proxy", "利润同比体现资费、网络成本、折旧和数字化业务贡献", statement),
            _metric("gross_margin", "综合毛利率", v.get("gpMargin"), _pct(v.get("gpMargin") or 0), _higher(v.get("gpMargin") or 0, .22, .42), "reported", "资费、网络运营成本和业务结构共同形成的结果", statement),
            _metric("net_margin", "综合净利率", v.get("npMargin"), _pct(v.get("npMargin") or 0), _higher(v.get("npMargin") or 0, .05, .15), "reported", "通信规模优势最终转化为净利润的效率", statement),
        ]),
        _group("network", "网络投资与第二曲线代理", [
            _metric("asset_growth", "总资产同比", v.get("YOYAsset"), _pct(v.get("YOYAsset") or 0), _lower(v.get("YOYAsset") or 0, .02, .14), "proxy", "资产增长代理网络投资与云基础设施扩张，需结合资本开支公告复核", statement),
            _metric("asset_turnover", "资产周转率", v.get("AssetTurnRatio"), _ratio(v.get("AssetTurnRatio") or 0), _higher(v.get("AssetTurnRatio") or 0, .28, .65), "reported", "网络资产每单位形成收入的效率", statement),
            _metric("cfo_to_revenue", "经营现金流/收入", v.get("CFOToOR"), _pct(v.get("CFOToOR") or 0), _higher(v.get("CFOToOR") or 0, .22, .42), "reported", "衡量订阅收入形成经营现金的能力", statement),
            _metric("receivable_days", "应收账款周转天数", v.get("NRTurnDays"), _days(v.get("NRTurnDays") or 0), _lower(v.get("NRTurnDays") or 0, 25, 100), "reported", "政企业务和云项目账期增长会拖累第二曲线现金质量", statement),
            _metric("current_asset_turnover", "流动资产周转率", v.get("CATurnRatio"), _ratio(v.get("CATurnRatio") or 0), _higher(v.get("CATurnRatio") or 0, .8, 3), "reported", "观察应收、现金和其他流动资产使用效率", statement),
        ]),
        _group("finance", "自由现金、资本结构与派息承载", [
            _metric("cfo_to_np", "经营现金流/净利润", v.get("CFOToNP"), _ratio(v.get("CFOToNP") or 0), _higher(v.get("CFOToNP") or 0, 1.8, 4.0), "reported", "运营商折旧较高，经营现金通常应显著高于净利润", statement),
            _metric("liability_ratio", "资产负债率", v.get("liabilityToAsset"), _pct(v.get("liabilityToAsset") or 0), _lower(v.get("liabilityToAsset") or 0, .28, .60), "reported", "低杠杆为网络升级和派息承诺保留空间", statement),
            _metric("quick_ratio", "速动比率", v.get("quickRatio"), _ratio(v.get("quickRatio") or 0), _higher(v.get("quickRatio") or 0, .5, 1.3), "reported", "短期现金资产对流动负债的覆盖", statement),
            _metric("roe_annualized", "年化ROE", v.get("roe_annualized"), _pct(v.get("roe_annualized") or 0), _higher(v.get("roe_annualized") or 0, .06, .14), "derived", "结合资产周转与派息率判断资本效率", statement),
            _metric("cfo_annualized", "年化经营现金流代理", v.get("cfo_annualized"), _hundred_million(v.get("cfo_annualized") or 0), _higher(v.get("CFOToNP") or 0, 1.8, 4.0), "derived", "按报告季度年化，尚未扣除资本开支，不能直接当自由现金流", statement),
        ]),
        _group("return", "派息、自由现金代理与估值", _market_metrics(v, snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)),
    ]


def build_industry_panorama(
    industry_id: str,
    snapshot: FinancialSnapshot,
    *,
    current_price: float,
    current_pe: float | None,
    current_pb: float | None,
    valuation_percentile: float,
    volatility: float,
    drawdown: float,
) -> IndustryPanorama | None:
    if industry_id == "hydro":
        groups = _hydro_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 25
    elif industry_id == "consumer":
        groups = _consumer_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 28
    elif industry_id == "resources":
        groups = _resources_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 26
    elif industry_id == "oilgas":
        groups = _oilgas_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 27
    elif industry_id == "tollroad":
        groups = _tollroad_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 26
    elif industry_id == "nuclear":
        groups = _nuclear_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 26
    elif industry_id == "telecom":
        groups = _telecom_groups(snapshot, current_price, current_pe, current_pb, valuation_percentile, volatility, drawdown)
        expected = 24
    else:
        return None
    actual = sum(len(group.metrics) for group in groups)
    return IndustryPanorama(
        report_date=snapshot.report_date,
        published_date=snapshot.published_date,
        coverage_ratio=min(1.0, actual / expected),
        groups=groups,
    )
