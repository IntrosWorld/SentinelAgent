"""General desktop protocol and runner tests; never touch the user's desktop."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import sentinel_agent.computer_use.desktop_agent as agent_module
from sentinel_agent.computer_use.desktop_agent import AdaptiveDesktopAgent, desktop_goal_matches
from sentinel_agent.computer_use.desktop_schema import (
    DesktopAction, DesktopElement, DesktopFailure, DesktopSnapshot, DesktopTask,
    StaleDesktopObservation, validate_desktop_action,
)


def snapshot(identifier="s1", text="", app="fixture.exe", **overrides):
    data = dict(
        id=identifier, window_id="123", app=app, title="Fixture", width=640, height=480,
        text=text, elements=[
            DesktopElement(id=f"{identifier}-button", role="Button", name="Apply", bounds=(20, 20, 100, 60)),
            DesktopElement(id=f"{identifier}-edit", role="Edit", name="Content", bounds=(20, 80, 300, 160), editable=True),
            DesktopElement(id=f"{identifier}-disabled", role="Button", name="Disabled", bounds=(400, 20, 500, 60), enabled=False),
            DesktopElement(id=f"{identifier}-password", role="Edit", name="Password", bounds=(400, 80, 500, 160), editable=True, password=True),
        ],
    )
    data.update(overrides)
    return DesktopSnapshot(**data)


def task(**overrides):
    data = dict(goal="Enter the requested result", expect_text="Result ready")
    data.update(overrides)
    return DesktopTask(**data)


def action(kind="wait", identifier="s1", **fields):
    return DesktopAction(kind=kind, snapshot_id=identifier, **fields)


class FakeSession:
    def __init__(self, snapshots=None, errors=None, validation_errors=None):
        self.snapshots = snapshots or [snapshot()]
        self.errors = list(errors or [])
        self.validation_errors = list(validation_errors or [])
        self.started = 0
        self.closed = 0
        self.observations = 0
        self.validations = []
        self.act_calls = []
        self.dispatched = []
        self.events = []

    async def start(self):
        self.started += 1

    async def observe(self):
        value = self.snapshots[min(self.observations, len(self.snapshots) - 1)]
        self.observations += 1
        return value

    async def validate(self, chosen):
        self.events.append("validate")
        self.validations.append(chosen)
        error = self.validation_errors.pop(0) if self.validation_errors else None
        if error:
            raise error

    async def act(self, chosen):
        self.events.append("act")
        self.act_calls.append(chosen)
        error = self.errors.pop(0) if self.errors else None
        if error:
            raise error
        self.dispatched.append(chosen)

    async def close(self):
        self.closed += 1


class FakePlanner:
    def __init__(self, choices=()):
        self.choices = list(choices)
        self.calls = []

    async def next_action(self, goal, current, history):
        self.calls.append((goal, current, list(history)))
        chosen = self.choices.pop(0) if self.choices else action(identifier=current.id)
        if isinstance(chosen, Exception):
            raise chosen
        return chosen


async def approve_all(chosen, current):
    return True


@pytest.mark.parametrize("chosen", [
    action("click", element_id="s1-button"), action("double_click", x=20, y=20),
    action("hover", element_id="s1-button"), action("drag", x=100, y=200, end_x=200, end_y=300),
    action("type", element_id="s1-edit", text="Hello world"), action("press_key", key="ENTER"),
    action("scroll", direction="down", x=10, y=300), action("scroll", direction="up", element_id="s1-edit"),
    action("wait"), action("finish"),
])
def test_general_action_shapes_are_supported(chosen):
    validate_desktop_action(chosen, snapshot())
    assert DesktopAction.model_validate_json(chosen.model_dump_json()) == chosen


@pytest.mark.parametrize("fields", [
    {"kind": "click"}, {"kind": "click", "x": 10},
    {"kind": "click", "element_id": "s1-button", "x": 10, "y": 10},
    {"kind": "double_click", "x": 10, "y": 10, "text": "bad"},
    {"kind": "hover", "element_id": "s1-button", "direction": "down"},
    {"kind": "drag", "x": 10, "y": 10, "end_x": 20},
    {"kind": "drag", "element_id": "s1-button", "x": 10, "y": 10, "end_x": 20, "end_y": 20},
    {"kind": "type", "x": 10, "y": 10, "text": "bad"},
    {"kind": "type", "element_id": "s1-edit"},
    {"kind": "press_key", "key": "WIN+R"}, {"kind": "press_key", "key": "ALT+F4"},
    {"kind": "press_key", "key": "CTRL+L"}, {"kind": "press_key", "key": "ENTER", "element_id": "s1-edit"},
    {"kind": "scroll", "direction": "down"}, {"kind": "scroll", "x": 10, "y": 10},
    {"kind": "wait", "x": 10, "y": 10}, {"kind": "finish", "text": "Success"},
    {"kind": "shell", "text": "calc.exe"}, {"kind": "wait", "script": "unsafe()"},
])
def test_missing_irrelevant_or_dangerous_action_fields_are_rejected(fields):
    with pytest.raises(ValidationError):
        DesktopAction(snapshot_id="s1", **fields)


@pytest.mark.parametrize("bad_coordinate", [True, 1.0, "1", float("nan"), float("inf"), -1])
def test_coordinates_are_strict_nonnegative_finite_integers(bad_coordinate):
    with pytest.raises(ValidationError):
        action("click", x=bad_coordinate, y=10)


@pytest.mark.parametrize("bounds", [(0, 0, 0, 10), (-1, 0, 10, 10), (0, 5, 10, 4), (0, 0, True, 10), (0, 0, 1.5, 10)])
def test_element_rectangles_are_strict_and_positive(bounds):
    with pytest.raises(ValidationError):
        DesktopElement(id="e", role="Button", name="Action", bounds=bounds)


def test_snapshot_rejects_out_of_frame_or_duplicate_elements():
    element = DesktopElement(id="e", role="Button", name="Action", bounds=(0, 0, 100, 100))
    with pytest.raises(ValidationError):
        snapshot(width=99, elements=[element])
    with pytest.raises(ValidationError):
        snapshot(elements=[element, element])


@pytest.mark.parametrize("chosen", [
    action("click", identifier="old", element_id="s1-button"),
    action("click", element_id="invented"), action("click", element_id="s1-disabled"),
    action("click", element_id="s1-password"), action("type", element_id="s1-button", text="bad"),
    action("type", element_id="s1-password", text="bad"), action("click", x=400, y=80),
    action("click", x=640, y=0), action("click", x=0, y=480),
    action("drag", x=20, y=200, end_x=640, end_y=300),
    action("drag", x=380, y=100, end_x=520, end_y=100),
])
def test_action_grounding_rejects_unknown_sensitive_and_outside_targets(chosen):
    with pytest.raises(ValueError):
        validate_desktop_action(chosen, snapshot())


@pytest.mark.parametrize("fields", [
    {"goal": "   "}, {"expect_text": "\n\t"}, {"max_seconds": float("nan")},
    {"max_seconds": float("inf")}, {"max_seconds": 901}, {"max_steps": True},
    {"max_steps": 51}, {"max_recoveries": -1},
])
def test_task_limits_and_nonblank_conditions(fields):
    with pytest.raises(ValidationError):
        task(**fields)


def test_observations_are_frozen():
    with pytest.raises(ValidationError):
        snapshot().width = 10
    with pytest.raises(ValidationError):
        snapshot().elements[0].name = "Changed"


@pytest.mark.asyncio
async def test_independent_content_text_verification_needs_no_model_or_input():
    session = FakeSession([snapshot(text="RESULT \n\t READY")])
    planner = FakePlanner()
    result = await AdaptiveDesktopAgent(planner, session).run(task())
    assert result.status == "completed"
    assert result.reason == "goal-verified"
    assert result.steps == 0
    assert planner.calls == session.act_calls == []
    assert session.started == session.closed == 1


def test_title_labels_and_screenshot_do_not_self_certify_success():
    observed = snapshot(title="Result ready", screenshot="Result ready", elements=[
        DesktopElement(id="e", role="Button", name="Result ready", bounds=(0, 0, 20, 20)),
    ])
    assert not desktop_goal_matches(task(), observed)
    assert not desktop_goal_matches(task(expect_text=None), observed)


@pytest.mark.asyncio
@pytest.mark.parametrize("app", ["notepad.exe", "mspaint.exe", "unfamiliar-fixture.exe"])
async def test_same_feedback_loop_handles_different_apps_without_recipes(app):
    session = FakeSession([snapshot(app=app), snapshot("s2", app=app), snapshot("s3", text="Result ready", app=app)])
    choices = [action("type", element_id="s1-edit", text="Requested content"), action("click", "s2", element_id="s2-button")]
    planner = FakePlanner(choices)

    async def approve(chosen, current):
        session.events.append("approve")
        return True

    result = await AdaptiveDesktopAgent(planner, session, approve).run(task())
    assert result.status == "completed"
    assert result.steps == 2
    assert [entry[1].id for entry in planner.calls] == ["s1", "s2"]
    assert session.dispatched == choices
    assert session.events == ["validate", "approve", "validate", "act"] * 2
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("chosen", [
    action("click", element_id="s1-button"), action("double_click", x=20, y=20),
    action("hover", x=20, y=20), action("drag", x=10, y=200, end_x=100, end_y=300),
    action("type", element_id="s1-edit", text="Hello"), action("press_key", key="ENTER"),
    action("scroll", x=10, y=200, direction="down"),
])
async def test_every_input_action_defaults_to_denied(chosen):
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner([chosen]), session).run(task())
    assert result.reason == "approval-denied"
    assert result.status == "failed"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("approval", [False, None, "yes", 1])
async def test_only_literal_true_is_an_approval(approval):
    async def approve(chosen, current):
        return approval
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner([action("click", element_id="s1-button")]), session, approve).run(task())
    assert result.reason == "approval-denied"
    assert session.act_calls == []


@pytest.mark.asyncio
async def test_wait_can_observe_again_without_approval():
    session = FakeSession([snapshot(), snapshot("s2", text="Result ready")])
    result = await AdaptiveDesktopAgent(FakePlanner(), session).run(task())
    assert result.status == "completed"
    assert result.steps == 1
    assert session.events == ["validate", "act"]


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,status,reason", [
    (None, "needs_review", "visual-verification-required"),
    ("Result ready", "failed", "unverified-finish"),
])
async def test_model_finish_is_not_proof_of_success(expected, status, reason):
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner([action("finish")]), session).run(task(expect_text=expected))
    assert (result.status, result.reason) == (status, reason)
    assert session.act_calls == session.validations == []
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", [
    None, {"kind": "shell", "snapshot_id": "s1"},
    action("click", element_id="invented"), action("click", "old", element_id="s1-button"),
    action("type", element_id="s1-password", text="secret"), action("click", x=640, y=0),
])
async def test_invalid_model_proposals_never_reach_backend_or_approval(proposal):
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner([proposal]), session, approve_all).run(task())
    assert result.reason == "invalid-action"
    assert session.act_calls == session.validations == []


@pytest.mark.asyncio
async def test_change_during_approval_replans_without_dispatching_old_action():
    session = FakeSession(
        [snapshot(), snapshot("s2"), snapshot("s3", text="Result ready")],
        validation_errors=[None, StaleDesktopObservation("Changed during approval")],
    )
    choices = [action("click", element_id="s1-button"), action("click", "s2", element_id="s2-button")]
    result = await AdaptiveDesktopAgent(FakePlanner(choices), session, approve_all).run(task())
    assert result.status == "completed"
    assert session.dispatched == [choices[1]]
    assert result.steps == 1
    assert {"kind": "recovery", "reason": "stale-observation", "attempt": 1} in result.trace


@pytest.mark.asyncio
async def test_pre_dispatch_stale_action_recovery_is_bounded():
    session = FakeSession(errors=[StaleDesktopObservation("changed"), StaleDesktopObservation("changed")])
    result = await AdaptiveDesktopAgent(FakePlanner(), session).run(task(max_recoveries=1))
    assert result.reason == "stale-limit"
    assert len(session.act_calls) == 2
    assert result.steps == 0


@pytest.mark.asyncio
async def test_uncertain_dispatch_is_terminal_not_retried():
    session = FakeSession(errors=[DesktopFailure("Input may already have happened")])
    planner = FakePlanner([action("click", element_id="s1-button")])
    result = await AdaptiveDesktopAgent(planner, session, approve_all).run(task())
    assert result.reason == "desktop-error"
    assert len(session.act_calls) == len(planner.calls) == 1
    assert result.steps == 0
    assert session.closed == 1


@pytest.mark.asyncio
async def test_step_limit_still_allows_last_action_to_be_verified():
    session = FakeSession([snapshot(), snapshot("s2", text="Result ready")])
    result = await AdaptiveDesktopAgent(FakePlanner(), session).run(task(max_steps=1))
    assert result.status == "completed"
    assert result.steps == 1
    failing_session = FakeSession()
    failed = await AdaptiveDesktopAgent(FakePlanner(), failing_session).run(task(max_steps=2))
    assert failed.reason == "step-limit"
    assert len(failing_session.dispatched) == 2


@pytest.mark.asyncio
async def test_slow_planner_is_cancelled_at_deadline():
    class SlowPlanner:
        async def next_action(self, *args):
            await asyncio.Event().wait()
    session = FakeSession()
    result = await asyncio.wait_for(AdaptiveDesktopAgent(SlowPlanner(), session).run(task(max_seconds=0.03)), 2)
    assert result.reason == "time-limit"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_approval_past_deadline_cannot_dispatch(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    async def slow_approval(chosen, current):
        clock[0] = 2.0
        return True
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner([action("click", element_id="s1-button")]), session, slow_approval).run(task(max_seconds=1))
    assert result.reason == "time-limit"
    assert session.act_calls == []


@pytest.mark.asyncio
async def test_precancelled_task_starts_no_session():
    cancel = asyncio.Event()
    cancel.set()
    session = FakeSession()
    result = await AdaptiveDesktopAgent(FakePlanner(), session).run(task(), cancel)
    assert result.status == "cancelled"
    assert session.started == 0
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["planning", "approval"])
async def test_stop_interrupts_inflight_planning_or_approval_without_input(stage):
    cancel, waiting = asyncio.Event(), asyncio.Event()
    class SlowPlanner:
        async def next_action(self, *args):
            waiting.set()
            await asyncio.Event().wait()
    async def slow_approval(chosen, current):
        waiting.set()
        await asyncio.Event().wait()
    session = FakeSession()
    planner = SlowPlanner() if stage == "planning" else FakePlanner([action("click", element_id="s1-button")])
    agent = AdaptiveDesktopAgent(planner, session, slow_approval)
    running = asyncio.create_task(agent.run(task(), cancel))
    await asyncio.wait_for(waiting.wait(), 2)
    cancel.set()
    result = await asyncio.wait_for(running, 2)
    assert result.status == "cancelled"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_trace_omits_screen_content_goals_and_typed_values():
    private = ["Private task goal", "Private screenshot text", "Private typed value"]
    session = FakeSession([snapshot(text=private[1]), snapshot("s2"), snapshot("s3", text="Result ready")])
    planner = FakePlanner([action("type", element_id="s1-edit", text=private[2]), action("wait", "s2")])
    result = await AdaptiveDesktopAgent(planner, session, approve_all).run(task(goal=private[0]))
    assert result.status == "completed"
    logged = result.model_dump_json() + repr([call[2] for call in planner.calls])
    assert all(value not in logged for value in private)


@pytest.mark.asyncio
async def test_planner_error_has_no_hidden_fallback_or_sensitive_error_trace():
    session = FakeSession()
    planner = FakePlanner([RuntimeError("Secret token should not be logged")])
    result = await AdaptiveDesktopAgent(planner, session).run(task())
    assert result.reason == "planner-error"
    assert "Secret token" not in result.model_dump_json()
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_start_and_cleanup_errors_are_sanitized_and_cleanup_is_attempted():
    class FailedSession(FakeSession):
        async def start(self):
            raise DesktopFailure("Private window title")
        async def close(self):
            self.closed += 1
            raise RuntimeError("Private process details")
    session = FailedSession()
    result = await AdaptiveDesktopAgent(FakePlanner(), session).run(task())
    assert result.reason == "desktop-error"
    assert {"kind": "cleanup", "reason": "close-error"} in result.trace
    assert "Private" not in result.model_dump_json()
    assert session.closed == 1
