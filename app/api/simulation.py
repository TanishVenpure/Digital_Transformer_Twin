from fastapi import APIRouter, Depends

from app.api.agent import require_simulation_access
from app.schemas.simulation import SimulationStartRequest, SimulationStatusResponse
from app.simulation.simulator import SimulationManager, get_simulator

# The simulator is one shared, process-wide instance with no transformer scope, so
# every route (including future ones) requires the explicit @simulation permission.
router = APIRouter(
    prefix="/simulation", tags=["simulation"],
    dependencies=[Depends(require_simulation_access)],
)


@router.post("/start", response_model=SimulationStatusResponse)
def start(
    body: SimulationStartRequest | None = None,
    simulator: SimulationManager = Depends(get_simulator),
):
    return simulator.start(body.speed if body else 1.0)


@router.post("/pause", response_model=SimulationStatusResponse)
def pause(simulator: SimulationManager = Depends(get_simulator)):
    return simulator.pause()


@router.post("/reset", response_model=SimulationStatusResponse)
def reset(simulator: SimulationManager = Depends(get_simulator)):
    return simulator.reset()


@router.post("/step", response_model=SimulationStatusResponse)
def step(simulator: SimulationManager = Depends(get_simulator)):
    return simulator.step()


@router.get("/status", response_model=SimulationStatusResponse)
def status(simulator: SimulationManager = Depends(get_simulator)):
    return simulator.status()
