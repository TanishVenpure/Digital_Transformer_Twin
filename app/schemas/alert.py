from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.alerts.types import MaintenanceType


class AlertResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transformer_id: str
    timestamp: datetime
    parameter: str
    formula_name: str
    formula_value: float
    threshold: float
    comparison_operator: str
    severity: str
    message: str
    maintenance_type: MaintenanceType | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    formula_inputs: dict[str, Any]
    acknowledged: bool
    resolved: bool
    resolved_at: datetime | None
    created_at: datetime


class AlertHistoryResponse(BaseModel):
    """Agent-friendly pagination across all stored alert lifecycle states."""
    items: list[AlertResponse]
    limit: int
    offset: int
    next_offset: int | None
