"""Calls into the Blender (bpy) model layer.

The bpy implementation arrives in the next push. Until then every call is
recorded and answered with status PENDING_BLENDER so the API, dashboard and
signalling flow can be exercised end to end.
"""
from datetime import datetime, timezone
from typing import Any

from app.twin3d.selector import BASE_MODEL, ModelSelection, ModelsConfig


class BlenderBridge:
    def __init__(self, config: ModelsConfig, history_size: int = 200):
        self.config = config
        self.current_model: str | None = None
        self.calls: list[dict[str, Any]] = []
        self.history_size = history_size

    def _record(self, action: str, **payload: Any) -> dict[str, Any]:
        call = {
            "action": action, "status": "PENDING_BLENDER",
            "called_at": datetime.now(timezone.utc).isoformat(), **payload,
        }
        self.calls.append(call)
        del self.calls[:-self.history_size]
        return call

    def _spec(self, model_id: str) -> dict[str, Any]:
        if model_id not in self.config.models:
            raise KeyError(f"Unknown model: {model_id}")
        return self.config.models[model_id].model_dump()

    # --- calls to be implemented with bpy -------------------------------------------------

    def load_base_model(self) -> dict[str, Any]:
        """TODO(bpy): open the base .blend scene."""
        self.current_model = BASE_MODEL
        return self._record("load_base_model", model_id=BASE_MODEL, model=self._spec(BASE_MODEL))

    def switch_model(self, selection: ModelSelection, *, source: str, reading_time: str | None = None) -> dict[str, Any]:
        """TODO(bpy): swap the displayed scene to the selected IoT model (or back to base)."""
        previous = self.current_model
        self.current_model = selection.model_id
        return self._record(
            "switch_model", model_id=selection.model_id, previous_model=previous,
            model=self._spec(selection.model_id), iot_slot=selection.iot_slot,
            signal=selection.signal.value if selection.signal else None,
            source=source, reading_time=reading_time,
        )

    def export_model(self, model_id: str) -> dict[str, Any]:
        """TODO(bpy): export the model to glTF for the dashboard viewer."""
        return self._record("export_model", model_id=model_id, model=self._spec(model_id))

    def signal(self, selection: ModelSelection, *, source: str, reading_time: str | None = None) -> dict[str, Any] | None:
        """Switch only when the selected model changes. Returns the call made, if any."""
        if selection.model_id == self.current_model:
            return None
        return self.switch_model(selection, source=source, reading_time=reading_time)
