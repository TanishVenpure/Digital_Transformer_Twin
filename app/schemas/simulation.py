from enum import Enum

from pydantic import BaseModel, Field


class SimulationStatus(str, Enum):
    STOPPED = "STOPPED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"


class SimulationStartRequest(BaseModel):
    speed: float = Field(default=1.0, gt=0, allow_inf_nan=False)


class SimulationStatusResponse(BaseModel):
    status: SimulationStatus
    current_step: int
    speed: float
