"""Interfaces used by real, CSV, or mock market-data sources."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class PricePbPoint:
    date: str
    close: float
    pb: float


class HistoricalMarketDataSource(ABC):
    @abstractmethod
    def get_daily_price_pb(self, stock_code: str, start_date: str, end_date: str) -> list[PricePbPoint]:
        """Return actual daily close prices and PB for the requested period."""
