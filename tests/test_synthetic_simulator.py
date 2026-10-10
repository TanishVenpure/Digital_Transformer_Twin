from datetime import timezone

from app.agent.models import MonitoringModule, MonitoringStatus
from app.agent.synthetic_simulator import (
    SyntheticScenarioName,
    SyntheticSimulatorConfig,
    SyntheticTransformerSimulator,
    evaluate_all,
    evaluate_scenario,
)


def test_same_seed_and_configuration_are_reproducible():
    first = SyntheticTransformerSimulator(SyntheticSimulatorConfig(seed=42)).generate(
        SyntheticScenarioName.HIGH_CURRENT
    )
    second = SyntheticTransformerSimulator(SyntheticSimulatorConfig(seed=42)).generate(
        SyntheticScenarioName.HIGH_CURRENT
    )
    assert first.event.model_dump(mode="json") == second.event.model_dump(mode="json")
    assert [record.model_dump(mode="json") for record in first.telemetry] == [
        record.model_dump(mode="json") for record in second.telemetry
    ]


def test_generated_records_have_shapes_timestamps_units_and_synthetic_labels():
    scenario = SyntheticTransformerSimulator().generate(SyntheticScenarioName.NORMAL)
    assert scenario.synthetic is True
    assert scenario.event.synthetic is True
    assert scenario.event.timestamp.tzinfo == timezone.utc
    assert scenario.thresholds.illustrative is True
    assert scenario.thresholds.source == "synthetic_prototype_only"
    assert all(record.is_synthetic for record in scenario.telemetry)
    assert all(record.transformer_id == scenario.transformer_id for record in scenario.telemetry)
    assert scenario.telemetry[0].units["I_A"] == "A"
    assert scenario.telemetry[0].data_quality == "valid"


def test_all_required_scenario_types_are_present_and_evaluable():
    simulator = SyntheticTransformerSimulator()
    results = evaluate_all(simulator)
    assert {result.scenario for result in results} == set(SyntheticScenarioName)
    assert all(result.passed for result in results), results


def test_trend_scenarios_are_consistent_with_their_narratives():
    simulator = SyntheticTransformerSimulator()
    oil = simulator.generate(SyntheticScenarioName.FALLING_OIL_LEVEL)
    temperatures = simulator.generate(SyntheticScenarioName.RAPID_TEMPERATURE_RISE)
    assert [record.measurements["oil_level"] for record in oil.telemetry] == [72.0, 64.0, 55.0]
    assert [record.measurements["top_oil_temperature"] for record in temperatures.telemetry] == [62.0, 72.0, 84.0, 97.0]


def test_missing_stale_conflicting_and_injection_scenarios_are_explicit():
    simulator = SyntheticTransformerSimulator()
    missing = simulator.generate(SyntheticScenarioName.MISSING_PHASE_READINGS)
    stale = simulator.generate(SyntheticScenarioName.STALE_READINGS)
    conflict = simulator.generate(SyntheticScenarioName.CONFLICTING_EVIDENCE)
    injection = simulator.generate(SyntheticScenarioName.PROMPT_INJECTION_NOTE)
    assert "I_C" not in missing.telemetry[0].measurements
    assert missing.telemetry[0].data_quality == "missing"
    assert stale.telemetry[0].data_quality == "stale"
    assert len({record.measurements["I_A"] for record in conflict.telemetry}) == 2
    assert "ignore all previous instructions" in injection.maintenance_history[0].notes.lower()


def test_critical_result_remains_visible_with_good_aggregate_output():
    scenario = SyntheticTransformerSimulator().generate(SyntheticScenarioName.CRITICAL_WITH_GOOD_AGGREGATE)
    statuses = {result.module: result.status for result in scenario.monitoring_results}
    assert statuses[MonitoringModule.WINDING_HEALTH] == MonitoringStatus.CRITICAL
    assert scenario.event.health_engine_output["health_score"] == 0.92
    assert scenario.event.health_engine_output["maintenance_priority"] == "normal"
    assert evaluate_scenario(scenario).passed


def test_scenarios_use_distinct_transformer_ids_when_configured():
    first = SyntheticTransformerSimulator(SyntheticSimulatorConfig(transformer_id="TX-A")).generate(
        SyntheticScenarioName.NORMAL
    )
    second = SyntheticTransformerSimulator(SyntheticSimulatorConfig(transformer_id="TX-B")).generate(
        SyntheticScenarioName.NORMAL
    )
    assert first.transformer_id != second.transformer_id
    assert first.event.transformer_id == "TX-A"
    assert second.event.transformer_id == "TX-B"
