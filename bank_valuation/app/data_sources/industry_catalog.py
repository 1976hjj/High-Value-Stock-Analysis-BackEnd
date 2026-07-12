"""Curated defensive-industry universe shared by analysis and backtests.

The qualitative scores are explicit research priors, not live company facts.
Market-driven factors are calculated separately from point-in-time price data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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


@dataclass(frozen=True)
class IndustryStockProfile:
    code: str
    name: str
    industry_id: IndustryId
    defense_score: float
    income_score: float
    quality_score: float
    dividend_yield_prior: float


@dataclass(frozen=True)
class IndustryProfile:
    industry_id: IndustryId
    name: str
    defensive_score: float
    risk_budget: float
    primary_risk: str
    valuation_metric: Literal["pb", "pe"]
    stocks: tuple[IndustryStockProfile, ...]


def _stock(
    industry_id: IndustryId,
    code: str,
    name: str,
    defense: float,
    income: float,
    quality: float,
    dividend_yield: float,
) -> IndustryStockProfile:
    market = "sh" if code.startswith(("6", "9")) else "sz"
    return IndustryStockProfile(
        code=f"{market}.{code}",
        name=name,
        industry_id=industry_id,
        defense_score=defense,
        income_score=income,
        quality_score=quality,
        dividend_yield_prior=dividend_yield,
    )


INDUSTRY_ORDER: tuple[IndustryId, ...] = (
    "telecom",
    "hydro",
    "bank",
    "tollroad",
    "nuclear",
    "oilgas",
    "resources",
    "consumer",
)


INDUSTRY_CATALOG: dict[IndustryId, IndustryProfile] = {
    "telecom": IndustryProfile(
        "telecom", "电信运营商", 94, 18, "资费政策与资本开支", "pe",
        (
            _stock("telecom", "600941", "中国移动", 95, 92, 94, .050),
            _stock("telecom", "601728", "中国电信", 90, 84, 88, .042),
            _stock("telecom", "600050", "中国联通", 84, 78, 82, .032),
        ),
    ),
    "hydro": IndustryProfile(
        "hydro", "大型水电", 96, 16, "来水、电价与估值", "pe",
        (
            _stock("hydro", "600900", "长江电力", 97, 86, 97, .035),
            _stock("hydro", "600025", "华能水电", 92, 78, 91, .028),
            _stock("hydro", "600674", "川投能源", 90, 84, 90, .038),
            _stock("hydro", "600886", "国投电力", 86, 79, 87, .032),
            _stock("hydro", "600236", "桂冠电力", 84, 88, 85, .045),
            _stock("hydro", "002039", "黔源电力", 80, 82, 82, .035),
        ),
    ),
    "bank": IndustryProfile(
        "bank", "优质银行", 86, 20, "信用风险与净息差", "pb",
        (
            _stock("bank", "600036", "招商银行", 91, 83, 94, .040),
            _stock("bank", "601398", "工商银行", 90, 91, 90, .052),
            _stock("bank", "601939", "建设银行", 89, 90, 89, .050),
            _stock("bank", "601288", "农业银行", 91, 92, 89, .054),
            _stock("bank", "601658", "邮储银行", 88, 83, 88, .042),
            _stock("bank", "601169", "北京银行", 80, 87, 80, .055),
        ),
    ),
    "tollroad": IndustryProfile(
        "tollroad", "成熟收费公路", 87, 12, "收费期限与车流", "pe",
        (
            _stock("tollroad", "600377", "宁沪高速", 91, 90, 92, .048),
            _stock("tollroad", "600350", "山东高速", 86, 91, 84, .055),
            _stock("tollroad", "001965", "招商公路", 87, 85, 87, .043),
            _stock("tollroad", "600012", "皖通高速", 84, 89, 84, .052),
            _stock("tollroad", "600548", "深高速", 82, 83, 82, .045),
        ),
    ),
    "nuclear": IndustryProfile(
        "nuclear", "核电", 88, 13, "高负债与资本开支", "pe",
        (
            _stock("nuclear", "601985", "中国核电", 89, 77, 90, .025),
            _stock("nuclear", "003816", "中国广核", 91, 84, 91, .034),
        ),
    ),
    "oilgas": IndustryProfile(
        "oilgas", "综合油气", 72, 10, "油价周期", "pe",
        (
            _stock("oilgas", "600938", "中国海油", 79, 89, 90, .058),
            _stock("oilgas", "601857", "中国石油", 76, 88, 84, .050),
            _stock("oilgas", "600028", "中国石化", 73, 86, 80, .060),
        ),
    ),
    "resources": IndustryProfile(
        "resources", "煤炭金属资源", 64, 8, "商品价格与政策", "pb",
        (
            _stock("resources", "601088", "中国神华", 82, 95, 92, .065),
            _stock("resources", "601225", "陕西煤业", 74, 91, 86, .070),
            _stock("resources", "600188", "兖矿能源", 65, 87, 76, .060),
            _stock("resources", "601899", "紫金矿业", 68, 66, 88, .018),
            _stock("resources", "600547", "山东黄金", 70, 58, 78, .008),
            _stock("resources", "603993", "洛阳钼业", 61, 62, 82, .020),
        ),
    ),
    "consumer": IndustryProfile(
        "consumer", "消费龙头", 82, 15, "品牌、需求与估值", "pe",
        (
            _stock("consumer", "600519", "贵州茅台", 90, 73, 98, .025),
            _stock("consumer", "000858", "五粮液", 84, 72, 91, .030),
            _stock("consumer", "600887", "伊利股份", 87, 82, 89, .045),
            _stock("consumer", "603288", "海天味业", 84, 62, 90, .018),
            _stock("consumer", "000333", "美的集团", 83, 78, 93, .038),
            _stock("consumer", "600690", "海尔智家", 80, 72, 90, .030),
            _stock("consumer", "000568", "泸州老窖", 82, 75, 92, .030),
            _stock("consumer", "600600", "青岛啤酒", 85, 73, 90, .025),
            _stock("consumer", "603195", "公牛集团", 86, 65, 94, .028),
            _stock("consumer", "002032", "苏泊尔", 84, 85, 91, .040),
        ),
    ),
}


STOCK_CATALOG = {
    stock.code: stock
    for industry in INDUSTRY_CATALOG.values()
    for stock in industry.stocks
}


def normalize_stock_code(stock_code: str) -> str:
    compact = stock_code.strip().lower()
    if compact.startswith(("sh.", "sz.")) and len(compact) == 9:
        return compact
    digits = "".join(character for character in compact if character.isdigit())[-6:]
    if len(digits) != 6:
        raise ValueError(f"无效 A 股代码: {stock_code}")
    return f"{'sh' if digits.startswith(('6', '9')) else 'sz'}.{digits}"


def profiles_for_industries(industry_ids: list[str]) -> list[IndustryStockProfile]:
    output: list[IndustryStockProfile] = []
    for industry_id in industry_ids:
        profile = INDUSTRY_CATALOG.get(industry_id)  # type: ignore[arg-type]
        if profile is None:
            raise ValueError(f"未知行业: {industry_id}")
        output.extend(profile.stocks)
    return output


def get_stock_profile(industry_id: str, stock_code: str) -> IndustryStockProfile:
    industry = INDUSTRY_CATALOG.get(industry_id)  # type: ignore[arg-type]
    if industry is None:
        raise ValueError(f"未知行业: {industry_id}")
    normalized = normalize_stock_code(stock_code)
    stock = next((item for item in industry.stocks if item.code == normalized), None)
    if stock is None:
        raise ValueError(f"{stock_code} 不在 {industry.name} 核心股票池中")
    return stock
