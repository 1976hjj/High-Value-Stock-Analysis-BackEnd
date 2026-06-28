"""Bank-specific metrics sourced from AKShare's Eastmoney financial endpoint."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import logging
import math

logger = logging.getLogger("bank_valuation.akshare")


@dataclass(frozen=True)
class BankSpecialMetrics:
    provision_coverage: float | None
    nim: float | None
    npl_ratio: float | None
    cet1_ratio: float | None
    capital_adequacy_ratio: float | None
    net_interest_spread: float | None
    loan_provision_ratio: float | None
    loan_to_deposit_ratio: float | None
    report_date: date
    notice_date: date
    source: str = "AKShare / Eastmoney financial indicators"


def _akshare_symbol(code: str) -> str:
    market, number = code.split(".", 1)
    return f"{number}.{market.upper()}"


def fetch_bank_special_metrics(code: str, as_of: date) -> BankSpecialMetrics | None:
    """Return latest announced bank-specific metrics on/before ``as_of``.

    Eastmoney fields BLDKBBL, NET_INTEREST_MARGIN, NET_INTEREST_SPREAD,
    NONPERLOAN, LOAN_PROVISION_RATIO, HXYJBCZL and NEWCAPITALADER are
    percentage points and are divided by 100. Loan-to-deposit ratio is derived
    from reported GROSSLOANS / TOTALDEPOSITS. The model uses decimal rates.
    """
    try:
        import akshare as ak
        frame = ak.stock_financial_analysis_indicator_em(symbol=_akshare_symbol(code))
        if frame is None or frame.empty:
            logger.warning("Bank special metrics unavailable: code=%s reason=empty response", code)
            return None
        required = {"REPORT_DATE", "NOTICE_DATE"}
        if not required.issubset(frame.columns):
            logger.warning("Bank special metrics unavailable: code=%s reason=missing report dates", code)
            return None
        frame["NOTICE_DATE"] = frame["NOTICE_DATE"].astype("datetime64[ns]")
        frame["REPORT_DATE"] = frame["REPORT_DATE"].astype("datetime64[ns]")
        valid = frame[frame["NOTICE_DATE"].dt.date <= as_of].copy()
        if valid.empty:
            logger.warning("Bank special metrics unavailable: code=%s as_of=%s reason=no announced report", code, as_of)
            return None
        valid = valid.sort_values(["REPORT_DATE", "NOTICE_DATE"], ascending=False)
        row = valid.iloc[0]
        def rate(field: str) -> float | None:
            raw = row.get(field)
            try:
                value = float(raw)
                return value / 100 if value >= 0 else None
            except (TypeError, ValueError):
                return None
        def ratio(numerator_field: str, denominator_field: str) -> float | None:
            try:
                numerator, denominator = float(row.get(numerator_field)), float(row.get(denominator_field))
                return numerator / denominator if math.isfinite(numerator) and math.isfinite(denominator) and denominator > 0 else None
            except (TypeError, ValueError):
                return None
        result = BankSpecialMetrics(
            provision_coverage=rate("BLDKBBL"), nim=rate("NET_INTEREST_MARGIN"), npl_ratio=rate("NONPERLOAN"),
            cet1_ratio=rate("HXYJBCZL"), capital_adequacy_ratio=rate("NEWCAPITALADER"),
            net_interest_spread=rate("NET_INTEREST_SPREAD"), loan_provision_ratio=rate("LOAN_PROVISION_RATIO"),
            loan_to_deposit_ratio=ratio("GROSSLOANS", "TOTALDEPOSITS"),
            report_date=row["REPORT_DATE"].date(), notice_date=row["NOTICE_DATE"].date(),
        )
        logger.info("Bank special metrics fetched: code=%s coverage=%s nim=%s npl=%s cet1=%s car=%s spread=%s ldr=%s report_date=%s", code, result.provision_coverage, result.nim, result.npl_ratio, result.cet1_ratio, result.capital_adequacy_ratio, result.net_interest_spread, result.loan_to_deposit_ratio, result.report_date)
        return result
    except Exception:
        logger.warning("Bank special metrics fetch failed: code=%s as_of=%s", code, as_of, exc_info=True)
        return None
