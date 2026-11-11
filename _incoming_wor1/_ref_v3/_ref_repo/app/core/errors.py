"""Domain error taxonomy for the NOVA backend.

Every gameplay rule violation raises a :class:`NovaError` subclass. The API
layer converts them into a single, machine-readable error envelope::

    {
      "error": {
        "code": "precondition_failed",
        "message": "Close the valve before repairing the pipe.",
        "details": {"unmet": [...]}
      },
      "request_id": "..."
    }

The frontend can therefore branch on ``error.code`` instead of parsing prose.
"""

from __future__ import annotations

from typing import Any


class NovaError(Exception):
    """Base class for all expected (non-bug) failures."""

    code: str = "nova_error"
    status_code: int = 500
    default_message: str = "Unexpected NOVA backend error."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return {"error": payload}

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<{type(self).__name__} code={self.code} message={self.message!r}>"


# ---------------------------------------------------------------------------
# 404 – missing entities
# ---------------------------------------------------------------------------
class NotFoundError(NovaError):
    code = "not_found"
    status_code = 404
    default_message = "The requested resource does not exist."


class SessionNotFoundError(NotFoundError):
    code = "session_not_found"
    default_message = "Game session not found."


class MissionNotFoundError(NotFoundError):
    code = "mission_not_found"
    default_message = "Mission not found."


class SimulationNotFoundError(NotFoundError):
    code = "simulation_not_found"
    default_message = "No simulation run exists for this mission."


class SampleNotFoundError(NotFoundError):
    code = "sample_not_found"
    default_message = "Science sample not found."


class ReportNotFoundError(NotFoundError):
    code = "report_not_found"
    default_message = "Report not found."


# ---------------------------------------------------------------------------
# 409 – gameplay rule conflicts
# ---------------------------------------------------------------------------
class ConflictError(NovaError):
    code = "conflict"
    status_code = 409
    default_message = "The request conflicts with the current game state."


class GameRuleError(ConflictError):
    """Base class for gameplay rule violations."""

    code = "game_rule_violation"


class PreconditionFailedError(GameRuleError):
    code = "precondition_failed"
    default_message = "This action is not possible yet."


class InsufficientResourceError(GameRuleError):
    code = "insufficient_resource"
    default_message = "Not enough resources for this action."


class MissingItemError(GameRuleError):
    code = "missing_item"
    default_message = "A required item is missing from the inventory."


class EquipmentFailureError(GameRuleError):
    code = "equipment_failure"
    default_message = "The tool malfunctioned. Maintain or replace it."


class MissionLockedError(GameRuleError):
    code = "mission_locked"
    default_message = "This mission is still locked."


class MissionCompletedError(GameRuleError):
    code = "mission_completed"
    default_message = "This mission has already been completed."


class MissionNotActiveError(GameRuleError):
    code = "mission_not_active"
    default_message = "Start the mission before performing actions."


class SimulationStateError(GameRuleError):
    code = "simulation_state"
    default_message = "The simulation is not in a state that allows this."


class StateConflictError(ConflictError):
    code = "state_conflict"
    default_message = "The game state changed concurrently. Retry the request."


class SessionLimitError(ConflictError):
    code = "session_limit"
    default_message = "The server reached its concurrent session limit."


# ---------------------------------------------------------------------------
# 422 – invalid input beyond pydantic validation
# ---------------------------------------------------------------------------
class InvalidInputError(NovaError):
    code = "invalid_input"
    status_code = 422
    default_message = "The request payload is invalid."


class UnknownActionError(InvalidInputError):
    code = "unknown_action"
    default_message = "Unknown mission action."


# ---------------------------------------------------------------------------
# 401 / 403 / 429 / 502
# ---------------------------------------------------------------------------
class AuthenticationError(NovaError):
    code = "unauthorized"
    status_code = 401
    default_message = "Missing or invalid credentials."


class ForbiddenError(NovaError):
    code = "forbidden"
    status_code = 403
    default_message = "You may not modify this game session."


class RateLimitError(NovaError):
    code = "rate_limited"
    status_code = 429
    default_message = "Too many requests. Slow down."


class ExternalServiceError(NovaError):
    code = "external_service_error"
    status_code = 502
    default_message = "An upstream data provider failed."


class ContentError(NovaError):
    """Raised at startup when bundled content files are malformed."""

    code = "content_error"
    status_code = 500
    default_message = "Bundled game content is invalid."
