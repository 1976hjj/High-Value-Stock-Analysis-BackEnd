"""Small, presentation-oriented A-share bank profiles (not investment ratings)."""
from __future__ import annotations
from ..models import BankProfile

_JOINT_STOCK = {"sh.600036": ("股份制商业银行", "零售与财富管理特色", "CMB"), "sh.601818": ("股份制商业银行", "综合金融协同", "CEB"), "sh.601166": ("股份制商业银行", "绿色金融与综合经营", "CIB"), "sh.600000": ("股份制商业银行", "上海市场化银行", "SPDB"), "sh.600016": ("股份制商业银行", "民营与中小企业服务", "CMBC"), "sh.600015": ("股份制商业银行", "综合金融服务", "HXB"), "sh.601998": ("股份制商业银行", "对公与综合金融", "CITIC")}
_STATE = {"sh.601398": ("国有大型商业银行", "综合化大型银行", "ICBC"), "sh.601939": ("国有大型商业银行", "基建与综合金融", "CCB"), "sh.601288": ("国有大型商业银行", "县域与三农金融", "ABC"), "sh.601988": ("国有大型商业银行", "跨境与国际业务", "BOC"), "sh.601328": ("国有大型商业银行", "综合交通金融", "BOCOM")}
_CITY = {"sh.601169": ("城市商业银行", "北京区域金融", "BOB"), "sh.601229": ("城市商业银行", "上海区域金融", "BOS"), "sh.601009": ("城市商业银行", "长三角区域金融", "NJB"), "sh.601838": ("城市商业银行", "成渝区域金融", "CDB"), "sh.601577": ("城市商业银行", "湖南区域金融", "CSB"), "sh.601963": ("城市商业银行", "重庆区域金融", "CQB"), "sz.002142": ("城市商业银行", "长三角区域金融", "NBCB"), "sz.002948": ("城市商业银行", "山东区域金融", "QDB"), "sz.002966": ("城市商业银行", "苏州区域金融", "SZB")}
_RURAL = {"sh.601128": ("农商行", "县域与小微金融", "CSRCB"), "sh.603323": ("农商行", "苏南县域金融", "SRCB"), "sz.002807": ("农商行", "江阴县域金融", "JRCB"), "sz.002839": ("农商行", "张家港县域金融", "ZRCB"), "sz.002958": ("农商行", "青岛县域金融", "QRCB")}


def bank_profile(code: str) -> BankProfile:
    if code in _STATE:
        bank_type, brief, logo = _STATE[code]; tone = "navy"
    elif code in _JOINT_STOCK:
        bank_type, brief, logo = _JOINT_STOCK[code]; tone = "coral"
    elif code in _CITY:
        bank_type, brief, logo = _CITY[code]; tone = "mint"
    elif code in _RURAL:
        bank_type, brief, logo = _RURAL[code]; tone = "violet"
    else:
        bank_type, brief, logo, tone = "商业银行", "银行金融服务", code.split(".")[-1], "mint"
    return BankProfile(bank_type=bank_type, brief=brief, logo_text=logo, logo_tone=tone)
