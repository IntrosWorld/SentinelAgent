"""Small, strict protocol shared by the browser, planner and run loop."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Element(StrictModel):
    id: str
    role: str
    name: str
    value: str = ""
    editable: bool = False


class Snapshot(StrictModel):
    id: str
    url: str
    title: str
    text: str
    elements: list[Element] = Field(default_factory=list)


class BrowserAction(StrictModel):
    kind: Literal["click", "fill", "scroll", "wait", "finish"]
    snapshot_id: str = Field(min_length=1)
    element_id: str | None = None
    text: str | None = Field(default=None, max_length=2000)
    direction: Literal["up", "down"] = "down"

    @model_validator(mode="after")
    def validate_fields(self):
        targeted = self.kind in {"click", "fill"}
        if targeted != (self.element_id is not None):
            raise ValueError("Only click/fill require an observed element ID")
        if (self.kind == "fill") != (self.text is not None):
            raise ValueError("Only fill requires text")
        return self


class BrowserTask(StrictModel):
    goal: str = Field(min_length=3, max_length=2000)
    url: str = Field(min_length=1, max_length=2000)
    expect_text: str = Field(min_length=1, max_length=300)
    expect_url: str | None = Field(default=None, max_length=2000)
    max_steps: int = Field(default=10, ge=1, le=50)
    max_seconds: float = Field(default=120, gt=0, le=600)
    max_recoveries: int = Field(default=2, ge=0, le=5)

    @field_validator("goal", "expect_text", "url")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A nonblank value is required")
        return value


class BrowserResult(StrictModel):
    status: Literal["completed", "failed", "cancelled"]
    reason: str
    steps: int
    trace: list[dict] = Field(default_factory=list)


class BrowserFailure(RuntimeError):
    """Value-free browser failure safe for a trace."""


class StaleObservation(BrowserFailure):
    pass
