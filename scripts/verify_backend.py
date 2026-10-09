"""Read-only skeleton checks; no credentials, payloads, or SQL errors printed."""
import importlib
import pkgutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.config.settings import get_settings
from app.database.db import get_engine
from app.main import create_app
from app.models import Base


def main() -> int:
    modules = list(pkgutil.walk_packages(app.__path__, prefix="app."))
    for module in modules:
        importlib.import_module(module.name)
    print(f"Imported {len(modules)} app modules")
    application = create_app()
    schema = application.openapi()
    for table in Base.metadata.sorted_tables:
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
    print(f"Compiled PostgreSQL DDL for {len(Base.metadata.tables)} tables")
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    print(f"Alembic configuration valid; migration heads: {scripts.get_heads()}")
    print("Application creation and OpenAPI generation passed")
    for path, operations in schema["paths"].items():
        for method in operations:
            if method in {"get", "post", "patch", "put", "delete"}:
                print(f"{method.upper():5} {path}")
    if get_settings().database_url is None:
        print("PostgreSQL checks skipped: DATABASE_URL is not configured")
    else:
        engine = get_engine()
        try:
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
            print("PostgreSQL connectivity passed")
        except Exception as exc:
            print(f"PostgreSQL connectivity failed ({type(exc).__name__}); details withheld")
            return 1
        finally:
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
