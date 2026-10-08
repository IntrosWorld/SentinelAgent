"""Strict, app-independent desktop observations and single-action proposals.

All coordinates are relative to the observed window, never the whole desktop.
An action is only a proposal: the executor must recheck the bound window and
observation immediately before dispatching it.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALLOWED_DESKTOP_KEYS = frozenset({
    "ENTER", "TAB", "SHIFT+TAB", "ESC", "SPACE", "BACKSPACE", "DELETE",
    "LEFT", "RIGHT", "UP", "DOWN", "HOME", "END", "PAGEUP", "PAGEDOWN",
    "CTRL+A", "CTRL+Z", "CTRL+Y",
})


class DesktopFailure(RuntimeError):
    """Value-free failure; input may have been dispatched and must not be retried."""


class StaleDesktopObservation(DesktopFailure):
    """The observation changed and the backend guarantees no input was dispatched."""


class StrictDesktopModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class DesktopElement(StrictDesktopModel):
    id: str = Field(min_length=1)
    role: str
    name: str
    bounds: tuple[int, int, int, int]
    enabled: bool = True
    editable: bool = False
    password: bool = False

    @model_validator(mode="after")
    def valid_bounds(self):
        left, top, right, bottom = self.bounds
        if not (0 <= left < right and 0 <= top < bottom):
            raise ValueError("Element bounds must describe a positive window-relative rectangle")
        return self


class DesktopSnapshot(StrictDesktopModel):
    id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    app: str = Field(min_length=1)
    title: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    elements: list[DesktopElement] = Field(default_factory=list)
    text: str = ""
    screenshot: str | None = None

    @model_validator(mode="after")
    def valid_elements(self):
        identifiers = [element.id for element in self.elements]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Observed element IDs must be unique")
        for element in self.elements:
            if element.bounds[2] > self.width or element.bounds[3] > self.height:
                raise ValueError("Element bounds must stay inside the observed window")
        return self


class DesktopAction(StrictDesktopModel):
    kind: Literal["click", "double_click", "hover", "drag", "type", "press_key", "scroll", "wait", "finish"]
    snapshot_id: str = Field(min_length=1)
    element_id: str | None = Field(default=None, min_length=1)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)
    end_x: int | None = Field(default=None, ge=0)
    end_y: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = None
    direction: Literal["up", "down"] | None = None

    @model_validator(mode="after")
    def action_fields(self):
        present = {field for field in ("element_id", "x", "y", "end_x", "end_y", "text", "key", "direction")
                   if getattr(self, field) is not None}
        target = {"element_id"} if self.element_id is not None else {"x", "y"}
        if self.kind in {"click", "double_click", "hover"}:
            expected = target
        elif self.kind == "drag":
            expected = {"x", "y", "end_x", "end_y"}
        elif self.kind == "type":
            expected = {"element_id", "text"}
        elif self.kind == "press_key":
            expected = {"key"}
        elif self.kind == "scroll":
            expected = target | {"direction"}
        else:
            expected = set()
        if present != expected:
            raise ValueError("Missing or irrelevant fields for this action kind")
        if self.kind == "press_key" and self.key not in ALLOWED_DESKTOP_KEYS:
            raise ValueError("The requested key is not allowlisted")
        return self


class DesktopTask(StrictDesktopModel):
    goal: str = Field(min_length=3, max_length=2000)
    expect_text: str | None = Field(default=None, min_length=1, max_length=300)
    max_steps: int = Field(default=12, ge=1, le=50)
    max_seconds: float = Field(default=180, gt=0, le=900, allow_inf_nan=False)
    max_recoveries: int = Field(default=2, ge=0, le=5)

    @field_validator("goal", "expect_text")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("A nonblank value is required")
        return value


class DesktopResult(StrictDesktopModel):
    status: Literal["completed", "failed", "cancelled", "needs_review"]
    reason: str
    steps: int = Field(ge=0)
    trace: list[dict] = Field(default_factory=list)


def validate_desktop_action(action: DesktopAction, snapshot: DesktopSnapshot) -> None:
    """Reject invented targets and out-of-frame or known password-field inputs.

    This is structural grounding, not a substitute for a backend's fresh-state
    validation. A coordinate inside a screenshot is not proof of its semantics.
    """
    if action.snapshot_id != snapshot.id:
        raise ValueError("Action refers to a different observation")
    if action.element_id is not None:
        target = next((element for element in snapshot.elements if element.id == action.element_id), None)
        if target is None or not target.enabled or target.password:
            raise ValueError("Target is missing, disabled, or sensitive")
        if action.kind == "type" and not target.editable:
            raise ValueError("Typing requires an observed editable target")
    points = []
    if action.x is not None:
        points.append((action.x, action.y))
    if action.end_x is not None:
        points.append((action.end_x, action.end_y))
    for x, y in points:
        if not (0 <= x < snapshot.width and 0 <= y < snapshot.height):
            raise ValueError("Coordinates must stay inside the observed window")
        if any(element.password and element.bounds[0] <= x < element.bounds[2]
               and element.bounds[1] <= y < element.bounds[3] for element in snapshot.elements):
            raise ValueError("Coordinates intersect a sensitive field")
    if action.kind == "drag":
        # Conservatively reject the entire drag's bounding box, not only its
        # endpoints: neither endpoint needs to be inside a crossed password box.
        left, right = sorted((action.x, action.end_x))
        top, bottom = sorted((action.y, action.end_y))
        if any(element.password and right >= element.bounds[0] and left < element.bounds[2]
               and bottom >= element.bounds[1] and top < element.bounds[3] for element in snapshot.elements):
            raise ValueError("Drag may cross a sensitive field")
