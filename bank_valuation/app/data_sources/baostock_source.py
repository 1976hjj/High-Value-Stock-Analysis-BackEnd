"""Baostock adapter for actual daily prices on networks reachable through a VPN.

``adjustflag=3`` requests unadjusted prices: the close actually traded on each
day, including the natural price change after ex-dividend/ex-rights dates. PB is
Baostock's ``pbMRQ`` series. This adapter only fetches market history; callers
map accounting data from any source into ``BankInput``.
"""
from __future__ import annotations
from .base import HistoricalMarketDataSource, PricePbPoint


class BaostockDataSource(HistoricalMarketDataSource):
    def get_daily_price_pb(self, stock_code: str, start_date: str, end_date: str) -> list[PricePbPoint]:
        try:
            import baostock as bs
        except ImportError as exc:  # pragma: no cover - dependency/environment case
            raise RuntimeError("请安装 baostock 后使用 BaostockDataSource") from exc
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"Baostock 登录失败: {login.error_msg}")
        try:
            query = bs.query_history_k_data_plus(
                stock_code,
                "date,code,close,pbMRQ",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",  # 不复权：交易日实际收盘价，除权除息后自然反映价格变化
            )
            if query.error_code != "0":
                raise RuntimeError(f"Baostock 查询失败: {query.error_msg}")
            rows: list[PricePbPoint] = []
            while query.next():
                date, _, close, pb = query.get_row_data()
                if close and pb and float(close) > 0 and float(pb) > 0:
                    rows.append(PricePbPoint(date=date, close=float(close), pb=float(pb)))
            return rows
        finally:
            bs.logout()

    # Compatibility alias for callers of the first development version.
    def get_adjusted_price_pb(self, stock_code: str, start_date: str, end_date: str) -> list[PricePbPoint]:
        return self.get_daily_price_pb(stock_code, start_date, end_date)
