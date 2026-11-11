from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.game import MissionStateResponse


MissionAction = Literal["scan_leak", "close_valve", "repair_pipe"]


class MissionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: MissionAction


class MissionActionResponse(BaseModel):
    success: bool
    event: str
    message: str
    mission_status: str
    next_objective: str | None
    resources: dict[str, int]
    inventory: dict[str, int]
    mission: MissionStateResponse
