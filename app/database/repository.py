"""PostgreSQL persistence and query construction; no domain calculations."""
from datetime import date, datetime
from typing import Any

from sqlalchemy import insert, select, union
from sqlalchemy.orm import Session

from app.models import Alert, DailyMetric, SensorReading


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def add_reading(self, *, transformer_id: str, timestamp: datetime, raw_data: dict[str, Any]):
        reading = SensorReading(transformer_id=transformer_id, timestamp=timestamp, raw_data=raw_data)
        self.session.add(reading)
        self.session.flush()
        return reading

    def add_readings(self, rows: list[dict[str, Any]]) -> int:
        if rows:
            self.session.execute(insert(SensorReading), rows)
        return len(rows)

    def store_daily_metric(self, metric: DailyMetric) -> DailyMetric:
        """Future aggregation supplies a complete metric, including actual formula outputs."""
        self.session.add(metric)
        self.session.flush()
        return metric

    def store_alerts(self, alerts: list[Alert]) -> None:
        """Future alert engine supplies alerts with preserved formula inputs."""
        self.session.add_all(alerts)
        self.session.flush()

    def alerts(
        self, *, transformer_id: str | None = None, parameter: str | None = None,
        severity: str | None = None, start_date: datetime | None = None,
        end_date: datetime | None = None, acknowledged: bool | None = None,
        resolved: bool | None = None, limit: int = 50, offset: int = 0,
    ) -> list[Alert]:
        query = select(Alert)
        for column, value in (
            (Alert.transformer_id, transformer_id), (Alert.parameter, parameter),
            (Alert.severity, severity), (Alert.acknowledged, acknowledged),
            (Alert.resolved, resolved),
        ):
            if value is not None:
                query = query.where(column == value)
        if start_date is not None:
            query = query.where(Alert.timestamp >= start_date)
        if end_date is not None:
            query = query.where(Alert.timestamp <= end_date)
        return list(self.session.scalars(
            query.order_by(Alert.timestamp.desc(), Alert.id.desc()).limit(limit).offset(offset)
        ).all())

    def get_alert(self, alert_id: int) -> Alert | None:
        return self.session.get(Alert, alert_id)

    def transformer_ids(self) -> list[str]:
        query = union(*[
            select(model.transformer_id) for model in (SensorReading, DailyMetric, Alert)
        ]).order_by("transformer_id")
        return list(self.session.scalars(query).all())

    def readings(
        self, transformer_id: str, *, start_date: datetime | None = None,
        end_date: datetime | None = None, limit: int = 50, offset: int = 0,
    ) -> list[SensorReading]:
        query = select(SensorReading).where(SensorReading.transformer_id == transformer_id)
        if start_date is not None:
            query = query.where(SensorReading.timestamp >= start_date)
        if end_date is not None:
            query = query.where(SensorReading.timestamp <= end_date)
        return list(self.session.scalars(
            query.order_by(SensorReading.timestamp.desc(), SensorReading.id.desc()).limit(limit).offset(offset)
        ).all())

    def daily_metrics(
        self, transformer_id: str, *, start_date: date | None = None,
        end_date: date | None = None, limit: int = 50, offset: int = 0,
    ) -> list[DailyMetric]:
        query = select(DailyMetric).where(DailyMetric.transformer_id == transformer_id)
        if start_date is not None:
            query = query.where(DailyMetric.date >= start_date)
        if end_date is not None:
            query = query.where(DailyMetric.date <= end_date)
        return list(self.session.scalars(
            query.order_by(DailyMetric.date.desc(), DailyMetric.id.desc()).limit(limit).offset(offset)
        ).all())
