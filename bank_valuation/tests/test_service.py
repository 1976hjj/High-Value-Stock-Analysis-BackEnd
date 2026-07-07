from bank_valuation.app.valuation.service import value_bank
from bank_valuation.app.valuation import mean_reversion
from bank_valuation.app.data_sources import bank_base
from datetime import date
from types import SimpleNamespace


def test_full_result_contains_auditable_inputs_and_all_rim_horizons(bank):
    result = value_bank(bank)
    assert result.input_snapshot["bps"] == bank.bps
    assert set(result.residual_income_prices) == {"3y", "5y", "10y"}
    assert result.residual_income_prices["5y"] == result.residual_income_price
    assert result.defensive_decision.buy_wait_price > 0
    assert result.defensive_decision.risk_light in {"green", "yellow", "red"}
    assert {item.name for item in result.defensive_decision.stress_tests} == {"分红压力托底", "悲观情景下沿", "危机情景下沿"}
    # Fixture has no date series, so the optional chart degrades gracefully.
    assert result.pb_history_chart == []


def test_data_loader_uses_short_lived_cache(monkeypatch, bank):
    calls = 0

    def fake_loader(code, requested_date):
        nonlocal calls
        calls += 1
        return bank

    bank_base._data_cache.clear()
    monkeypatch.setattr(bank_base, "_load_bank_input_once", fake_loader)
    first = bank_base.load_bank_input("601398", date(2025, 6, 10))
    second = bank_base.load_bank_input("601398", date(2025, 6, 10))
    assert calls == 1
    assert first.stock_code == second.stock_code


def test_bank_csv_cache_round_trips_snapshot_and_market_history(monkeypatch, tmp_path, bank):
    cached = bank.model_copy(update={
        "stock_code": "sh.601398",
        "market_date": date(2025, 6, 10), "financial_report_date": date(2025, 3, 31),
        "pb_history_dates": [date(2025, 6, 6), date(2025, 6, 9), date(2025, 6, 10)],
        "pb_history": [.65, .66, .67], "price_history": [5.9, 5.95, 6.0],
    })
    monkeypatch.setattr(bank_base, "_BANK_CACHE_DIR", tmp_path)
    bank_base._write_disk_cache(date(2025, 6, 10), cached)
    restored = bank_base._read_disk_cache("sh.601398", date(2025, 6, 10))
    assert restored is not None
    assert restored.current_price == 6.0
    assert restored.pb_history == [.65, .66, .67]
    assert restored.price_history == [5.9, 5.95, 6.0]


def test_trailing_dividend_deduplicates_same_cash_event(monkeypatch):
    rows_by_year = {
        "dividend 2026": [
            {
                "statYear": "2025",
                "dividOperateDate": "2026-01-15",
                "dividPayDate": "2026-01-15",
                "dividCashPsBeforeTax": "1.0",
            },
            {
                "statYear": "2025",
                "dividOperateDate": "2026-01-15",
                "dividPayDate": "2026-01-15",
                "dividCashPsBeforeTax": "1.0",
            },
        ],
        "dividend 2025": [
            {
                "statYear": "2024",
                "dividOperateDate": "2025-07-10",
                "dividPayDate": "2025-07-10",
                "dividCashPsBeforeTax": "2.0",
            },
            {
                "statYear": "2024",
                "dividOperateDate": "2025-07-05",
                "dividPayDate": "2025-07-05",
                "dividCashPsBeforeTax": "9.0",
            },
        ],
        "dividend 2024": [],
    }

    monkeypatch.setattr(bank_base, "bs", SimpleNamespace(query_dividend_data=lambda **kwargs: object()))
    monkeypatch.setattr(bank_base, "_rows", lambda query, source: rows_by_year[source])

    assert bank_base._trailing_dividend("sh.600036", date(2026, 7, 7)) == 3.0


def test_trailing_dividend_keeps_distinct_real_events(monkeypatch):
    rows_by_year = {
        "dividend 2026": [
            {
                "statYear": "2025",
                "dividOperateDate": "2026-01-15",
                "dividPayDate": "2026-01-15",
                "dividCashPsBeforeTax": "1.0",
            }
        ],
        "dividend 2025": [
            {
                "statYear": "2024",
                "dividOperateDate": "2025-07-10",
                "dividPayDate": "2025-07-10",
                "dividCashPsBeforeTax": "2.0",
            }
        ],
        "dividend 2024": [],
    }

    monkeypatch.setattr(bank_base, "bs", SimpleNamespace(query_dividend_data=lambda **kwargs: object()))
    monkeypatch.setattr(bank_base, "_rows", lambda query, source: rows_by_year[source])

    assert bank_base._trailing_dividend("sh.600036", date(2026, 7, 7)) == 3.0


def test_mean_reversion_overview_ranks_healthy_cheap_bank_above_risky_discount(monkeypatch, bank):
    healthy = bank.model_copy(update={
        "stock_code": "sh.601398",
        "pb_current": .67,
        "current_price": 6.0,
        "bps": 6.0 / .67,
        "pb_history": [.55 + i * .001 for i in range(800)],
    })
    risky = bank.model_copy(update={
        "stock_code": "sh.600000",
        "stock_name": "浦发银行",
        "current_price": 4.5,
        "bps": 15.0,
        "pb_current": .30,
        "pb_history": [.60 for _ in range(800)],
        "roe": .045,
        "profit_growth_yoy": -.08,
        "npl_ratio": .024,
        "provision_coverage": 1.35,
        "cet1_ratio": .079,
    })

    monkeypatch.setattr(mean_reversion, "BANK_NAMES", {"sh.601398": "工商银行", "sh.600000": "浦发银行"})
    monkeypatch.setattr(mean_reversion, "load_bank_input", lambda code, valuation_date, refresh_cache: healthy if code == "sh.601398" else risky)

    overview = mean_reversion.bank_mean_reversion_overview(date(2025, 6, 10), refresh_cache=True)

    assert overview.results[0].stock_code == "sh.601398"
    assert overview.results[0].status in {"high_conviction_reversion", "undervalued_watch"}
    assert overview.results[0].dividend_safety_score > 60
    assert overview.results[0].stable_growth_score > 50
    assert overview.results[0].income_status in {"core_income", "income_watch", "not_income_candidate"}
    assert overview.results[1].status == "risk_discount"
    assert overview.results[1].income_status == "yield_trap_risk"
    assert "风险型低估" in overview.results[1].tags
    assert overview.yield_trap_count == 1


def test_mean_reversion_overview_can_hide_risky_rows(monkeypatch, bank):
    risky = bank.model_copy(update={
        "stock_code": "sh.600000",
        "stock_name": "浦发银行",
        "current_price": 4.5,
        "bps": 15.0,
        "pb_current": .30,
        "pb_history": [.60 for _ in range(800)],
        "roe": .045,
        "profit_growth_yoy": -.08,
        "cet1_ratio": .079,
    })

    monkeypatch.setattr(mean_reversion, "BANK_NAMES", {"sh.600000": "浦发银行"})
    monkeypatch.setattr(mean_reversion, "load_bank_input", lambda code, valuation_date, refresh_cache: risky)

    overview = mean_reversion.bank_mean_reversion_overview(date(2025, 6, 10), refresh_cache=True, include_risky=False)

    assert overview.results == []
    assert overview.risky_count == 1
