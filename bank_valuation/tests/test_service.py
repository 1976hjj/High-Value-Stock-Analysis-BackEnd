from bank_valuation.app.valuation.service import value_bank
from bank_valuation.app.data_sources import bank_base
from datetime import date


def test_full_result_contains_auditable_inputs_and_all_rim_horizons(bank):
    result = value_bank(bank)
    assert result.input_snapshot["bps"] == bank.bps
    assert set(result.residual_income_prices) == {"3y", "5y", "10y"}
    assert result.residual_income_prices["5y"] == result.residual_income_price
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
