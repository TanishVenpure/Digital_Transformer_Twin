"""CSV -> readings. Columns are renamed by config/column_mapping.yaml; no units are inferred."""
import csv
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.alerts.configuration import PROJECT_ROOT


def load_column_mapping(path: Path | None = None) -> dict[str, str]:
    with (path or PROJECT_ROOT / "config" / "column_mapping.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    return config.get("mappings") or {}


def parse_value(text: str) -> Any:
    text = text.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return text


def read_csv(
    path: Path, *, mapping: dict[str, str] | None = None, timestamp_column: str = "timestamp",
    transformer_column: str = "transformer_id", default_transformer: str = "T1",
) -> Iterator[tuple[str, datetime, dict[str, Any]]]:
    mapping = load_column_mapping() if mapping is None else mapping
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            row = {mapping.get(column, column): value for column, value in row.items()}
            timestamp = datetime.fromisoformat(row.pop(timestamp_column).strip())
            transformer = (row.pop(transformer_column, None) or default_transformer).strip()
            # Empty cells are dropped so the IoT reports SKIPPED rather than INVALID.
            data = {column: value for column, value in ((c, parse_value(v or "")) for c, v in row.items()) if value is not None}
            yield transformer, timestamp, data
