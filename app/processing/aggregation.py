"""Future hourly-to-daily aggregation extension point."""
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd

from app.models import SensorReading


class DailyAggregator(ABC):
    @abstractmethod
    def aggregate(self, readings: list[SensorReading]) -> dict[str, Any]:
        """Choose approved parameters/statistics and aggregate a complete UTC day."""
        raise NotImplementedError


def readings_to_dataframe(readings: list[SensorReading]) -> pd.DataFrame:
    """Future implementation converts raw JSON payloads without guessing parameter names."""
    raise NotImplementedError("Dataset normalization and column policy are not configured")


def aggregate_daily_data(transformer_id: str, day: date) -> dict[str, Any]:
    """Retrieve hourly records, define completeness, aggregate, and persist DailyMetric later."""
    raise NotImplementedError("Daily aggregation rules are not configured")
