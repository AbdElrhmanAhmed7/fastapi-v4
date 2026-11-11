from pydantic import BaseModel, ConfigDict, Field, field_validator


class GameCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_name: str = Field(min_length=1, max_length=50)

    @field_validator("player_name")
    @classmethod
    def strip_player_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Player name cannot contain only whitespace")
        return value


class MissionStateResponse(BaseModel):
    id: str
    status: str
    current_objective: str | None
    leak_detected: bool
    valve_closed: bool
    pipe_repaired: bool
    leak_active: bool


class GameStateResponse(BaseModel):
    game_id: str
    player_name: str
    resources: dict[str, int]
    inventory: dict[str, int]
    missions: dict[str, MissionStateResponse]
