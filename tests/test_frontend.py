from app.alerts.configuration import load_thresholds
from app.config.settings import PROJECT_ROOT, Settings, get_settings
from app.formulas.registry import formula_registry


def test_root_still_reports_service(client):
    assert client.get("/").json()["status"] == "running"


def test_twin_page_is_served(client):
    redirect = client.get("/twin", follow_redirects=False)
    assert redirect.status_code in (307, 308) and redirect.headers["location"] == "/twin/"
    page = client.get("/twin/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    for script in ("engine.js", "twin-api.js"):
        assert f'src="{script}"' in page.text
        assert client.get(f"/twin/{script}").status_code == 200


def test_twin_model_is_binary_gltf(client):
    model = client.get("/twin/transformer.glb")
    assert model.status_code == 200
    assert model.content[:4] == b"glTF"


def test_twin_page_posts_only_registered_rule_features(client):
    bridge = client.get("/twin/twin-api.js").text
    for name, formula in formula_registry.all().items():
        assert name in bridge
        for feature in formula.required_features:
            assert feature in bridge or feature.rsplit("_", 1)[0] + "_" in bridge


def test_api_routes_are_not_shadowed_by_frontend(client):
    assert client.get("/api/v1/health").json()["status"] == "healthy"
    assert client.get("/openapi.json").status_code == 200


def test_thresholds_file_defaults_to_production_config(monkeypatch):
    monkeypatch.delenv("THRESHOLDS_FILE")
    assert Settings(_env_file=None).thresholds_file == PROJECT_ROOT / "config" / "thresholds.yaml"


def test_demo_thresholds_enable_oil_rule(monkeypatch):
    monkeypatch.setenv("THRESHOLDS_FILE", "config/thresholds.demo.yaml")
    get_settings.cache_clear()
    try:
        demo = load_thresholds()
    finally:
        monkeypatch.setenv("THRESHOLDS_FILE", "config/thresholds.yaml")
        get_settings.cache_clear()
    production = load_thresholds()
    assert demo["oil_level_predictive_maintenance"].configured
    assert not production["oil_level_predictive_maintenance"].configured
    assert demo["3_phase_current_instrument_error"] == production["3_phase_current_instrument_error"]
    assert demo["3_phase_voltage_instrument_error"] == production["3_phase_voltage_instrument_error"]
