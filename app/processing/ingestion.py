"""CSV transport preview and future mapping configuration boundary."""
import csv
import io
from pathlib import Path
from typing import Any

import yaml

from app.schemas.sensor import UploadResponse
from app.services.errors import BadRequest, PayloadTooLarge

# Transport limits only; these are not transformer thresholds.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_ROWS = 5


def load_column_mapping(path: Path) -> dict[str, Any]:
    """Load caller-selected mapping config without assuming dataset column names."""
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    mappings = config.get("mappings", {})
    if not isinstance(mappings, dict):
        raise ValueError("mappings must be a YAML mapping")
    return mappings


def preview_csv(filename: str | None, content: bytes) -> UploadResponse:
    """Validate UTF-8 comma-separated tabular content; do not store or normalize it."""
    if not filename or Path(filename).suffix.lower() != ".csv":
        raise BadRequest("Supply a file with a .csv extension")
    if len(content) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("CSV upload exceeds the 2 MiB preview limit")
    if not content:
        raise BadRequest("CSV file is empty")
    try:
        text = content.decode("utf-8-sig")
        if "\x00" in text:
            raise ValueError("Binary content")
        reader = csv.reader(io.StringIO(text), strict=True)
        columns = next(reader)
        if not columns or any(not column.strip() for column in columns):
            raise ValueError("Missing header")
        if len(set(columns)) != len(columns):
            raise ValueError("Duplicate header")
        # A one-column CSV is legitimate. No dataset-specific schema is assumed.
        count = 0
        for row in reader:
            if not row:
                continue
            if len(row) != len(columns):
                raise ValueError("Inconsistent row width")
            count += 1
        if count == 0:
            raise ValueError("No data rows")
    except (UnicodeDecodeError, csv.Error, StopIteration, ValueError) as exc:
        raise BadRequest("Supply a UTF-8 CSV with a header and consistent data rows") from exc
    return UploadResponse(
        message="CSV upload preview accepted. Full dataset normalization and storage will be added later.",
        columns=columns, preview_rows=min(count, MAX_PREVIEW_ROWS), stored=False,
    )
