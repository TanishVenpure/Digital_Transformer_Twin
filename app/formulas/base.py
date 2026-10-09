from abc import ABC, abstractmethod
from typing import Any


class Formula(ABC):
    name: str

    @abstractmethod
    def calculate(self, data: dict[str, Any]) -> float:
        """Calculate an approved formula from explicitly defined inputs."""
        raise NotImplementedError
