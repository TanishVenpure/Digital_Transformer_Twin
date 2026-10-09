from threading import Lock

from fastapi import Request

from app.schemas.simulation import SimulationStatus, SimulationStatusResponse


class SimulationManager:
    """In-memory controls, scoped to one app process; no timer or sensor generation."""
    def __init__(self):
        self._lock = Lock()
        self._status = SimulationStatus.STOPPED
        self._current_step = 0
        self._speed = 1.0

    def _snapshot(self):
        return SimulationStatusResponse(
            status=self._status, current_step=self._current_step, speed=self._speed
        )

    def status(self):
        with self._lock:
            return self._snapshot()

    def start(self, speed: float = 1.0):
        with self._lock:
            self._status = SimulationStatus.RUNNING
            self._speed = speed
            return self._snapshot()

    def pause(self):
        with self._lock:
            if self._status == SimulationStatus.RUNNING:
                self._status = SimulationStatus.PAUSED
            return self._snapshot()

    def reset(self):
        with self._lock:
            self._status = SimulationStatus.STOPPED
            self._current_step = 0
            self._speed = 1.0
            return self._snapshot()

    def step(self):
        with self._lock:
            # Only advance bookkeeping. Future generated readings MUST go through
            # ProcessingService.receive_reading(); never write directly to the DB.
            self._current_step += 1
            return self._snapshot()


def get_simulator(request: Request) -> SimulationManager:
    return request.app.state.simulator
