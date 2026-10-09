from enum import Enum


class Scenario(str, Enum):
    """Future scenario identities only; deterioration behavior is not implemented."""
    NORMAL = "NORMAL"
    GRADUAL_DEGRADATION = "GRADUAL_DEGRADATION"
    SUDDEN_FAULT = "SUDDEN_FAULT"
    TEMPORARY_SPIKE = "TEMPORARY_SPIKE"
