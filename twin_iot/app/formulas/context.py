"""Calculation context supplied by services; formulas never query the database."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class HistoricalReading:
    timestamp: datetime
    data: dict[str, Any]
    reading_id: int | None = None


@dataclass(frozen=True)
class FormulaContext:
    timestamp: datetime | None = None
    previous_reading: HistoricalReading | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
