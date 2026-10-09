from app.schemas.sensor import SensorReadingCreate
from app.simulation.scenarios import Scenario


def generate_reading(scenario: Scenario, step: int) -> SensorReadingCreate:
    """Future generator returns the same schema as real incoming data.

    TODO: add approved transformer parameters and scenario models.
    Synthetic data must never bypass ProcessingService or its normal
    ingestion/processing/formula/alert pipeline.
    """
    raise NotImplementedError("Synthetic transformer generation is not configured")
