"""Blender bridge: state-tracked model switching for the 3D dashboard.

The dashboard's Three.js viewer (twin3d.js) handles all client-side 3D rendering
using the pre-exported transformer.glb and the IoT statuses from the timeline.
This bridge tracks which model is active server-side, gates redundant switches,
and records every call for the /api/twin3d/state diagnostic endpoint.

When bpy (Blender Python) is available, subclass BlenderBridge and override
_apply_load and _apply_switch to drive the Blender scene alongside the dashboard.
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

    def _record(self, action: str, status: str = "DONE", **payload: Any) -> dict[str, Any]:
        call = {
            "action": action, "status": status,
            "called_at": datetime.now(timezone.utc).isoformat(), **payload,
        }
        self.calls.append(call)
        del self.calls[:-self.history_size]
        return call

    def _spec(self, model_id: str) -> dict[str, Any]:
        if model_id not in self.config.models:
            raise KeyError(f"Unknown model: {model_id}")
        return self.config.models[model_id].model_dump()

    # --- bpy extension points (override in a subclass when Blender is available) ----------

    def _apply_load(self, model_id: str, spec: dict[str, Any]) -> None:
        """Override to open the base .blend scene in Blender."""

    def _apply_switch(self, model_id: str, spec: dict[str, Any], previous: str | None) -> None:
        """Override to swap the displayed Blender scene to the selected IoT model."""

    # --- public API -----------------------------------------------------------------------

    def load_base_model(self) -> dict[str, Any]:
        spec = self._spec(BASE_MODEL)
        self._apply_load(BASE_MODEL, spec)
        self.current_model = BASE_MODEL
        return self._record("load_base_model", model_id=BASE_MODEL, model=spec)

    def switch_model(self, selection: ModelSelection, *, source: str, reading_time: str | None = None) -> dict[str, Any]:
        previous = self.current_model
        spec = self._spec(selection.model_id)
        self._apply_switch(selection.model_id, spec, previous)
        self.current_model = selection.model_id
        return self._record(
            "switch_model", model_id=selection.model_id, previous_model=previous,
            model=spec, iot_slot=selection.iot_slot,
            signal=selection.signal.value if selection.signal else None,
            source=source, reading_time=reading_time,
        )

    def export_model(self, model_id: str) -> dict[str, Any]:
        return self._record("export_model", model_id=model_id, model=self._spec(model_id))

    def signal(self, selection: ModelSelection, *, source: str, reading_time: str | None = None) -> dict[str, Any] | None:
        """Switch only when the selected model changes. Returns the call made, if any."""
        if selection.model_id == self.current_model:
            return None
        return self.switch_model(selection, source=source, reading_time=reading_time)
