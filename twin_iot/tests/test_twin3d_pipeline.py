"""End-to-end tests: sensor reading -> IoT evaluation -> model selection -> Blender bridge.

Verifies that CRITICAL/WARNING readings select the correct iotN model, that the
bridge reports DONE (not PENDING_BLENDER), and that the signal gating works.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.alerts.configuration import load_thresholds
from app.alerts.types import Signal
from app.processing.ingestion import read_csv
from app.services.processing_service import ProcessingService
from app.twin3d.blender_bridge import BlenderBridge
from app.twin3d.selector import BASE_MODEL, ModelSelection, load_models, select_model

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
DEMO = Path(__file__).resolve().parents[1] / "data" / "examples" / "escalation_demo.csv"


@pytest.fixture(scope="module")
def config():
    return load_models()


@pytest.fixture(scope="module")
def thresholds():
    return load_thresholds()


@pytest.fixture
def bridge(config):
    b = BlenderBridge(config)
    b.load_base_model()
    return b


# --- model selection from evaluations --------------------------------------------------------

def test_all_normal_selects_base_model(config, thresholds):
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "current_r": 600, "current_y": 610, "current_b": 605,
        "reference_current_r": 600, "reference_current_y": 610, "reference_current_b": 605,
        "voltage_r": 250, "voltage_y": 251, "voltage_b": 249,
        "reference_voltage_r": 250, "reference_voltage_y": 251, "reference_voltage_b": 249,
        "oil_level": 70, "top_oil_temperature": 70, "ambient_temperature": 30,
        "winding_temperature_r": 62, "winding_temperature_y": 63, "winding_temperature_b": 61,
    })
    selection = select_model(result.evaluations, config)
    assert selection.model_id == BASE_MODEL
    assert selection.iot_slot is None
    assert selection.signal is None


def test_critical_oil_level_selects_iot5(config, thresholds):
    """Oil level below critical (20%) should select the iot5 model."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {"oil_level": 15.0})
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot5"
    assert selection.iot_slot == 5
    assert selection.signal == Signal.CRITICAL


def test_warning_oil_level_selects_iot5(config, thresholds):
    """Oil level at warning (40%) should select the iot5 model."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {"oil_level": 35.0})
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot5"
    assert selection.iot_slot == 5
    assert selection.signal == Signal.WARNING


def test_critical_top_oil_temp_selects_iot6(config, thresholds):
    """Top-oil temperature above trip (105) should select iot6."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "top_oil_temperature": 108.0, "ambient_temperature": 33.0,
    })
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot6"
    assert selection.iot_slot == 6
    assert selection.signal == Signal.CRITICAL


def test_critical_winding_temp_selects_iot7(config, thresholds):
    """Winding rise > 45 K should select iot7."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "winding_temperature_r": 78, "winding_temperature_y": 84, "winding_temperature_b": 77,
        "ambient_temperature": 33.0,
    })
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot7"
    assert selection.iot_slot == 7
    assert selection.signal == Signal.CRITICAL


def test_current_overload_selects_iot3(config, thresholds):
    """Current loading >= 100% should select iot3."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "current_r": 860, "current_y": 850, "current_b": 845,
    })
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot3"
    assert selection.iot_slot == 3
    assert selection.signal == Signal.CRITICAL


def test_sensor_error_selects_iot1(config, thresholds):
    """Current sensor error > 1% should select iot1 (MAINTENANCE_REQUIRED)."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "current_r": 620, "current_y": 610, "current_b": 605,
        "reference_current_r": 600, "reference_current_y": 610, "reference_current_b": 605,
    })
    selection = select_model(result.evaluations, config)
    assert selection.model_id == "iot1"
    assert selection.iot_slot == 1
    assert selection.signal == Signal.MAINTENANCE_REQUIRED


def test_worst_signal_wins_across_iots(config, thresholds):
    """When multiple IoTs signal, the worst status wins; ties go to the lowest slot."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "oil_level": 35.0,                     # IOT5 WARNING
        "top_oil_temperature": 108.0,           # IOT6 CRITICAL
        "ambient_temperature": 33.0,
    })
    selection = select_model(result.evaluations, config)
    # CRITICAL outranks WARNING
    assert selection.model_id == "iot6"
    assert selection.signal == Signal.CRITICAL
    # Both should appear in the signalling list
    slots = [slot for slot, _ in selection.signalling]
    assert 5 in slots and 6 in slots


def test_equal_severity_picks_lowest_slot(config, thresholds):
    """When two IoTs have the same worst signal, the lower slot number wins."""
    service = ProcessingService(thresholds)
    result = service.process("T1", NOW, {
        "oil_level": 35.0,                     # IOT5 WARNING
        "top_oil_temperature": 96.0,            # IOT6 WARNING (>= 95)
        "ambient_temperature": 33.0,
    })
    selection = select_model(result.evaluations, config)
    assert selection.iot_slot == 5  # lower slot wins


# --- bridge state tracking -------------------------------------------------------------------

def test_bridge_starts_at_base(bridge):
    assert bridge.current_model == BASE_MODEL


def test_bridge_reports_done_not_pending(bridge, config):
    selection = ModelSelection(
        model_id="iot5", label="Low oil level", iot_slot=5, signal=Signal.CRITICAL,
    )
    call = bridge.signal(selection, source="test", reading_time=NOW.isoformat())
    assert call is not None
    assert call["status"] == "DONE"
    assert call["action"] == "switch_model"
    assert call["model_id"] == "iot5"
    assert call["previous_model"] == BASE_MODEL
    assert call["iot_slot"] == 5
    assert call["signal"] == "CRITICAL"
    assert bridge.current_model == "iot5"


def test_bridge_no_op_on_same_model(bridge, config):
    """Signalling the same model twice should return None (no redundant call)."""
    selection = ModelSelection(model_id="iot5", label="Low oil level", iot_slot=5, signal=Signal.CRITICAL)
    bridge.signal(selection, source="test")
    again = bridge.signal(selection, source="test")
    assert again is None


def test_bridge_switches_back_to_base(bridge, config):
    bridge.signal(
        ModelSelection(model_id="iot5", label="Low oil level", iot_slot=5, signal=Signal.CRITICAL),
        source="test",
    )
    call = bridge.signal(
        ModelSelection(model_id=BASE_MODEL, label="Base transformer"),
        source="test",
    )
    assert call is not None
    assert call["model_id"] == BASE_MODEL
    assert call["previous_model"] == "iot5"
    assert call["status"] == "DONE"
    assert bridge.current_model == BASE_MODEL


def test_bridge_records_history(bridge, config):
    for i in range(1, 8):
        bridge.signal(
            ModelSelection(model_id=f"iot{i}", label=f"IoT {i}", iot_slot=i, signal=Signal.WARNING),
            source="test",
        )
    assert len(bridge.calls) >= 7  # load_base_model + 7 switches
    assert all(c["status"] == "DONE" for c in bridge.calls)


# --- full pipeline: demo CSV -> evaluations -> model selection -> bridge ----------------------

def test_demo_csv_pipeline_escalation(config, thresholds):
    """The escalation demo CSV starts normal and ends critical; the bridge must switch models."""
    readings = list(read_csv(DEMO))
    service = ProcessingService(thresholds)
    bridge = BlenderBridge(config)
    bridge.load_base_model()

    models_seen = []
    for transformer, timestamp, data in readings:
        result = service.process(transformer, timestamp, data)
        selection = select_model(result.evaluations, config)
        call = bridge.signal(selection, source="timeline", reading_time=timestamp.isoformat())
        models_seen.append((selection.model_id, call))

    # First reading: all normal -> base model, no switch (already at base)
    assert models_seen[0] == (BASE_MODEL, None)

    # Last reading: multiple IoTs are critical; bridge must have switched away from base
    last_model, last_call = models_seen[-1]
    assert last_model != BASE_MODEL
    assert last_model.startswith("iot")

    # At least one switch happened and all calls are DONE
    switches = [model_id for model_id, call in models_seen if call is not None]
    assert len(switches) >= 1
    assert all(c["status"] == "DONE" for c in bridge.calls)


def test_demo_csv_final_reading_picks_worst_iot(config, thresholds):
    """The last demo row has CRITICAL on IOT3-7; the lowest critical slot (3) should win."""
    readings = list(read_csv(DEMO))
    service = ProcessingService(thresholds)
    results = service.replay(readings)
    final = results[-1]
    selection = select_model(final.evaluations, config)
    # All transformer IoTs (3-7) are critical; slot 3 is lowest
    assert selection.model_id == "iot3"
    assert selection.iot_slot == 3
    assert selection.signal == Signal.CRITICAL


# --- bpy extension point ---------------------------------------------------------------------

def test_subclass_can_override_apply_methods(config):
    """The bridge is designed for bpy subclassing; verify the hook is called."""
    applied = []

    class BpyBridge(BlenderBridge):
        def _apply_load(self, model_id, spec):
            applied.append(("load", model_id))

        def _apply_switch(self, model_id, spec, previous):
            applied.append(("switch", model_id, previous))

    bridge = BpyBridge(config)
    bridge.load_base_model()
    assert applied == [("load", "base")]

    bridge.signal(
        ModelSelection(model_id="iot6", label="Top-oil", iot_slot=6, signal=Signal.CRITICAL),
        source="test",
    )
    assert applied[-1] == ("switch", "iot6", "base")
