import pytest
from bank_valuation.app.models import BankInput


@pytest.fixture
def bank() -> BankInput:
    return BankInput(
        stock_code="601398", stock_name="工商银行", current_price=6.0, bps=9.0, eps=.7, roe=.085,
        net_profit=360000000000, profit_growth_yoy=.01, dividend_per_share=.3, payout_ratio=.43,
        dividend_yield=.05, pb_current=.67, pe_current=8.5, pb_history=[.55 + i * .001 for i in range(800)],
        nim=.014, npl_ratio=.014, provision_coverage=2.0, cet1_ratio=.13, capital_adequacy_ratio=.19,
        risk_free_rate=.02, equity_risk_premium=.06, beta=.8, long_term_growth=.03,
    )
