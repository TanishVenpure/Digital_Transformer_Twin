"""Pick which 3D model to show from the seven IoT statuses."""
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.alerts.configuration import PROJECT_ROOT
from app.alerts.engine import IoTEvaluation
from app.alerts.types import Signal, SIGNAL_RANK

BASE_MODEL = "base"


class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    blend_file: str
    export_file: str
    focus_nodes: list[str] = Field(default_factory=list)
    iot_slot: int | None = Field(default=None, ge=1, le=8)


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger_signals: list[Signal]
    models: dict[str, ModelSpec]

    @model_validator(mode="after")
    def one_model_per_slot(self):
        if BASE_MODEL not in self.models:
            raise ValueError("A base model is required")
        slots = sorted(spec.iot_slot for spec in self.models.values() if spec.iot_slot is not None)
        if slots != list(range(1, 9)):
            raise ValueError("Exactly one model is required for each IoT slot 1..8")
        return self

    def for_slot(self, slot: int) -> str:
        return next(name for name, spec in self.models.items() if spec.iot_slot == slot)


class ModelSelection(BaseModel):
    model_id: str
    label: str
    iot_slot: int | None = None
    signal: Signal | None = None
    # Every IoT currently signalling, worst first: [(slot, signal)].
    signalling: list[tuple[int, Signal]] = Field(default_factory=list)


def load_models(path: Path | None = None) -> ModelsConfig:
    with (path or PROJECT_ROOT / "config" / "models.yaml").open(encoding="utf-8") as stream:
        return ModelsConfig.model_validate(yaml.safe_load(stream))


def select_model(evaluations: dict[str, IoTEvaluation] | list[IoTEvaluation], config: ModelsConfig) -> ModelSelection:
    items = evaluations.values() if isinstance(evaluations, dict) else evaluations
    signalling = sorted(
        ((e.iot_slot, e.status) for e in items if e.status in config.trigger_signals),
        key=lambda pair: (-SIGNAL_RANK[pair[1]], pair[0]),
    )
    if not signalling:
        return ModelSelection(model_id=BASE_MODEL, label=config.models[BASE_MODEL].label)
    slot, signal = signalling[0]
    model_id = config.for_slot(slot)
    return ModelSelection(model_id=model_id, label=config.models[model_id].label,
                          iot_slot=slot, signal=signal, signalling=signalling)
