from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    FORBIDDEN = "forbidden"


class RunStatus(StrEnum):
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class Action(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    expected_result: str
    rationale: str = ""


class Plan(BaseModel):
    goal: str
    actions: list[Action]


class Observation(BaseModel):
    action: Action
    success: bool
    message: str
    state: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    risk: RiskLevel
    allowed: bool
    requires_approval: bool = False
    reason: str


class RunEvent(BaseModel):
    kind: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentRun(BaseModel):
    id: str
    instruction: str
    status: RunStatus
    plan: Plan | None = None
    current_step: int = 0
    events: list[RunEvent] = Field(default_factory=list)
    pending_action: Action | None = None
    result: dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    instruction: str = Field(min_length=5, max_length=2000)
    use_model: bool = True


class ApprovalRequest(BaseModel):
    approved: bool

