from pathlib import Path
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr, ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable

from app.config.settings import Settings
from app.models import Base


def test_metadata_uses_postgresql_jsonb():
    assert set(Base.metadata.tables) == {"sensor_readings", "daily_metrics", "alerts"}
    for table in Base.metadata.sorted_tables:
        sql = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in sql
    assert isinstance(Base.metadata.tables["sensor_readings"].c.raw_data.type, JSONB)
    assert isinstance(Base.metadata.tables["daily_metrics"].c.aggregated_data.type, JSONB)
    assert isinstance(Base.metadata.tables["daily_metrics"].c.formula_results.type, JSONB)
    assert isinstance(Base.metadata.tables["alerts"].c.formula_inputs.type, JSONB)
    assert isinstance(Base.metadata.tables["alerts"].c.details.type, JSONB)


def test_reject_other_database_dialects():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=SecretStr("sqlite:///forbidden.db"))


def test_url_secret_is_masked():
    settings = Settings(_env_file=None, database_url=SecretStr(
        "postgresql+psycopg://user:private_password@localhost/db"
    ))
    assert "private_password" not in repr(settings)


def test_alembic_structure():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["0002"]
    assert scripts.get_revision("0002").down_revision == "0001"
    assert (Path(scripts.dir) / "env.py").is_file()


def test_alembic_revision_template(tmp_path):
    import shutil
    from alembic import command
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "versions").mkdir()
    shutil.copy("alembic/script.py.mako", folder / "script.py.mako")
    config = Config()
    config.set_main_option("script_location", str(folder))
    revision = command.revision(config, message="template verification")
    assert revision is not None
    source = Path(revision.path).read_text(encoding="utf-8")
    compile(source, revision.path, "exec")
