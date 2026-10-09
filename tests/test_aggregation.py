from datetime import date

import pytest

from app.processing.aggregation import aggregate_daily_data, readings_to_dataframe
from app.services.processing_service import ProcessingService


def test_aggregation_is_not_enabled():
    with pytest.raises(NotImplementedError):
        aggregate_daily_data("T01", date(2026, 10, 9))
    with pytest.raises(NotImplementedError):
        readings_to_dataframe([])


def test_completed_day_is_not_enabled(db):
    with pytest.raises(NotImplementedError):
        ProcessingService(db).process_completed_day("T01", date(2026, 10, 9))
    db.commit.assert_not_called()
