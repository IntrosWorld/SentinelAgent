"""Adaptive loop contracts, exercised without a browser or network access."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from sentinel_agent.computer_use.agent import AdaptiveBrowserAgent
import sentinel_agent.computer_use.agent as agent_module
from sentinel_agent.computer_use.planner import ACTIONS, BrowserPlanner
import sentinel_agent.computer_use.planner as planner_module
from sentinel_agent.computer_use.schema import (
    BrowserAction,
    BrowserFailure,
    BrowserTask,
    Element,
    Snapshot,
    StaleObservation,
)
from sentinel_agent.schemas import Action, Plan
from sentinel_agent.egress.client import EgressError
from sentinel_agent.llm import OllamaPlanner


START_URL = "https://example.test/start"
DONE_URL = "https://example.test/done"


def snapshot(identifier="s1", text="Not there yet", url=START_URL):
    return Snapshot(
        id=identifier,
        url=url,
        title="Local test page",
        text=text,
        elements=[
            Element(id=f"{identifier}-button", role="button", name="Continue"),
            Element(id=f"{identifier}-input", role="textbox", name="Query", editable=True),
        ],
    )


def task(**overrides):
    values = {"goal": "Find the receipt", "url": START_URL, "expect_text": "Receipt ready"}
    values.update(overrides)
    return BrowserTask(**values)


def action(kind="wait", identifier="s1", **kwargs):
    return BrowserAction(kind=kind, snapshot_id=identifier, **kwargs)


class FakeSession:
    def __init__(self, snapshots=None, errors=None):
        self.snapshots = snapshots or [snapshot()]
        self.errors = list(errors or [])
        self.started = []
        self.observations = 0
        self.act_calls = []
        self.dispatched = []
        self.closed = 0

    async def start(self, url):
        self.started.append(url)

    async def observe(self):
        item = self.snapshots[min(self.observations, len(self.snapshots) - 1)]
        self.observations += 1
        return item

    async def act(self, chosen):
        self.act_calls.append(chosen)
        error = self.errors.pop(0) if self.errors else None
        if error is not None:
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
        if not self.choices:
            return action(identifier=current.id)
        choice = self.choices.pop(0)
        if isinstance(choice, Exception):
            raise choice
        return choice


async def approve_all(chosen, current):
    return True


@pytest.mark.asyncio
async def test_initial_page_can_independently_satisfy_goal():
    session = FakeSession([snapshot(text="RECEIPT \n\t ready")])
    planner = FakePlanner()
    result = await AdaptiveBrowserAgent(planner, session).run(task())
    assert result.status == "completed"
    assert result.reason == "goal-verified"
    assert result.steps == 0
    assert planner.calls == []
    assert session.started == [START_URL]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_each_action_is_planned_against_fresh_feedback():
    pages = [snapshot(), snapshot("s2", "Confirm the result"), snapshot("s3", "Receipt ready")]
    first = action("click", element_id="s1-button")
    second = action("fill", "s2", element_id="s2-input", text="Hello")
    planner = FakePlanner([first, second])
    session = FakeSession(pages)
    seen_approvals = []

    async def approve(chosen, current):
        seen_approvals.append((chosen, current.id))
        return True

    result = await AdaptiveBrowserAgent(planner, session, approve=approve).run(task())
    assert result.status == "completed"
    assert result.steps == 2
    assert [call[1].id for call in planner.calls] == ["s1", "s2"]
    assert seen_approvals == [(first, "s1"), (second, "s2")]
    assert session.dispatched == [first, second]
    assert session.observations == 3
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["click", "fill"])
async def test_state_changing_actions_default_to_denied(kind):
    kwargs = {"element_id": f"s1-{'input' if kind == 'fill' else 'button'}"}
    if kind == "fill":
        kwargs["text"] = "Unapproved value"
    session = FakeSession()
    planner = FakePlanner([action(kind, **kwargs)])
    result = await AdaptiveBrowserAgent(planner, session).run(task())
    assert result.status == "failed"
    assert result.reason == "approval-denied"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_explicit_approval_denial_never_dispatches():
    session = FakeSession()

    async def deny(chosen, current):
        return False

    planner = FakePlanner([action("click", element_id="s1-button")])
    result = await AdaptiveBrowserAgent(planner, session, approve=deny).run(task())
    assert result.reason == "approval-denied"
    assert result.steps == 0
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wait", "scroll"])
async def test_read_only_actions_do_not_require_approval(kind):
    session = FakeSession([snapshot(), snapshot("s2", "Receipt ready")])
    planner = FakePlanner([action(kind)])
    result = await AdaptiveBrowserAgent(planner, session).run(task())
    assert result.status == "completed"
    assert len(session.dispatched) == 1


@pytest.mark.asyncio
async def test_model_cannot_self_certify_completion():
    session = FakeSession()
    planner = FakePlanner([action("finish")])
    result = await AdaptiveBrowserAgent(planner, session).run(task())
    assert result.status == "failed"
    assert result.reason == "unverified-finish"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("url,expected", [(DONE_URL, "completed"), (DONE_URL + "?other=1", "failed")])
async def test_url_condition_requires_exact_match_as_well_as_text(url, expected):
    session = FakeSession([snapshot(text="Receipt ready", url=url)])
    planner = FakePlanner([action("finish")])
    result = await AdaptiveBrowserAgent(planner, session).run(task(expect_url=DONE_URL))
    assert result.status == expected
    assert session.act_calls == []


@pytest.mark.asyncio
async def test_right_url_without_required_text_does_not_complete():
    session = FakeSession([snapshot(url=DONE_URL)])
    result = await AdaptiveBrowserAgent(FakePlanner([action("finish")]), session).run(task(expect_url=DONE_URL))
    assert result.reason == "unverified-finish"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chosen",
    [action("click", element_id="invented-button"), action("click", "old-snapshot", element_id="s1-button")],
)
async def test_unknown_targets_and_snapshot_ids_fail_closed(chosen):
    session = FakeSession()
    result = await AdaptiveBrowserAgent(FakePlanner([chosen]), session, approve=approve_all).run(task())
    assert result.status == "failed"
    assert result.reason == "invalid-action"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proposal",
    [
        {"kind": "navigate", "snapshot_id": "s1", "url": "https://unobserved.test"},
        {"kind": "wait", "snapshot_id": "s1", "text": "unexpected payload"},
        {"kind": "click", "snapshot_id": "s1", "element_id": "s1-button", "javascript": "untrusted()"},
        None,
    ],
)
async def test_invalid_model_action_schema_is_rejected_without_dispatch(proposal):
    session = FakeSession()
    result = await AdaptiveBrowserAgent(FakePlanner([proposal]), session, approve=approve_all).run(task())
    assert result.status == "failed"
    assert result.reason == "invalid-action"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_fill_requires_an_observed_editable_target():
    session = FakeSession()
    chosen = action("fill", element_id="s1-button", text="Do not type into this")
    result = await AdaptiveBrowserAgent(FakePlanner([chosen]), session, approve=approve_all).run(task())
    assert result.reason == "invalid-action"
    assert session.act_calls == []


@pytest.mark.asyncio
async def test_stale_observation_replans_before_a_new_dispatch():
    session = FakeSession(
        [snapshot(), snapshot("s2"), snapshot("s3", "Receipt ready")],
        errors=[StaleObservation("Element changed before dispatch")],
    )
    planner = FakePlanner([
        action("click", element_id="s1-button"),
        action("click", "s2", element_id="s2-button"),
    ])
    result = await AdaptiveBrowserAgent(planner, session, approve=approve_all).run(task(max_recoveries=1))
    assert result.status == "completed"
    assert result.steps == 1
    assert [call[1].id for call in planner.calls] == ["s1", "s2"]
    assert len(session.act_calls) == 2
    assert [chosen.element_id for chosen in session.dispatched] == ["s2-button"]
    assert session.closed == 1


@pytest.mark.asyncio
async def test_stale_recovery_is_bounded():
    session = FakeSession(errors=[StaleObservation("changed"), StaleObservation("changed")])
    result = await AdaptiveBrowserAgent(FakePlanner(), session).run(task(max_recoveries=1))
    assert result.status == "failed"
    assert result.reason == "stale-limit"
    assert len(session.act_calls) == 2
    assert result.steps == 0
    assert session.closed == 1


@pytest.mark.asyncio
async def test_ambiguous_post_dispatch_failure_is_not_retried():
    session = FakeSession(errors=[BrowserFailure("A click may have happened")])
    planner = FakePlanner([action("click", element_id="s1-button")])
    result = await AdaptiveBrowserAgent(planner, session, approve=approve_all).run(task())
    assert result.status == "failed"
    assert result.reason == "browser-error"
    assert len(session.act_calls) == 1
    assert len(planner.calls) == 1
    assert session.closed == 1


@pytest.mark.asyncio
async def test_step_budget_stops_further_actions():
    session = FakeSession()
    planner = FakePlanner()
    result = await AdaptiveBrowserAgent(planner, session).run(task(max_steps=2))
    assert result.status == "failed"
    assert result.reason == "step-limit"
    assert result.steps == 2
    assert len(session.dispatched) == 2
    assert len(planner.calls) == 2
    assert session.closed == 1


@pytest.mark.asyncio
async def test_last_permitted_action_can_still_complete():
    session = FakeSession([snapshot(), snapshot("s2", "Receipt ready")])
    result = await AdaptiveBrowserAgent(FakePlanner(), session).run(task(max_steps=1))
    assert result.status == "completed"
    assert result.steps == 1


@pytest.mark.asyncio
async def test_slow_planner_is_bounded_by_automated_time():
    session = FakeSession()

    class SlowPlanner:
        async def next_action(self, goal, current, history):
            await asyncio.Event().wait()

    result = await asyncio.wait_for(
        AdaptiveBrowserAgent(SlowPlanner(), session).run(task(max_seconds=0.03)), timeout=2,
    )
    assert result.reason == "time-limit"
    assert result.status == "failed"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_expired_budget_does_not_start_another_dispatch(monkeypatch):
    clock = [0.0]
    # Replace only this module's clock; leave asyncio's monotonic clock intact.
    monkeypatch.setattr(agent_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    session = FakeSession()
    planner = FakePlanner([action("click", element_id="s1-button")])

    async def approve_after_deadline(chosen, current):
        clock[0] = 2.0
        return True

    result = await AdaptiveBrowserAgent(planner, session, approve=approve_after_deadline).run(task(max_seconds=1))
    assert result.status == "failed"
    assert result.reason == "time-limit"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_cleanup_error_is_in_returned_trace_without_exception_details():
    class FailedCleanup(FakeSession):
        async def close(self):
            self.closed += 1
            raise BrowserFailure("Private cleanup URL and credentials")

    session = FailedCleanup([snapshot(text="Receipt ready")])
    result = await AdaptiveBrowserAgent(FakePlanner(), session).run(task())
    assert result.status == "completed"
    assert {"kind": "cleanup", "reason": "close-error"} in result.trace
    assert "Private cleanup" not in result.model_dump_json()
    assert session.closed == 1


@pytest.mark.asyncio
async def test_precancelled_run_performs_no_browser_actions():
    cancelled = asyncio.Event()
    cancelled.set()
    session = FakeSession()
    result = await AdaptiveBrowserAgent(FakePlanner(), session).run(task(), cancel=cancelled)
    assert result.status == "cancelled"
    assert session.started == []
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_cancellation_interrupts_a_pending_planner():
    cancelled = asyncio.Event()
    planning = asyncio.Event()
    session = FakeSession()

    class SlowPlanner:
        async def next_action(self, goal, current, history):
            planning.set()
            await asyncio.Event().wait()

    running = asyncio.create_task(AdaptiveBrowserAgent(SlowPlanner(), session).run(task(), cancel=cancelled))
    await asyncio.wait_for(planning.wait(), timeout=2)
    cancelled.set()
    result = await asyncio.wait_for(running, timeout=2)
    assert result.status == "cancelled"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_cancellation_during_approval_never_dispatches():
    cancelled = asyncio.Event()
    approving = asyncio.Event()
    session = FakeSession()

    async def pending_approval(chosen, current):
        approving.set()
        await asyncio.Event().wait()

    planner = FakePlanner([action("click", element_id="s1-button")])
    running = asyncio.create_task(
        AdaptiveBrowserAgent(planner, session, approve=pending_approval).run(task(), cancel=cancelled),
    )
    await asyncio.wait_for(approving.wait(), timeout=2)
    cancelled.set()
    result = await asyncio.wait_for(running, timeout=2)
    assert result.status == "cancelled"
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_model_exception_is_sanitized_and_has_no_fallback():
    private = "private-goal-and-key-sk-do-not-log-this"
    session = FakeSession()
    planner = FakePlanner([RuntimeError(private)])
    result = await AdaptiveBrowserAgent(planner, session).run(task(goal=private))
    assert result.status == "failed"
    assert result.reason == "planner-error"
    assert private not in result.model_dump_json()
    assert session.act_calls == []
    assert session.closed == 1


@pytest.mark.asyncio
async def test_trace_and_planner_history_omit_sensitive_inputs():
    goal = "private goal phrase never log this"
    page_text = "private body from an authenticated page"
    typed = "private typed password never log this"
    session = FakeSession([snapshot(text=page_text), snapshot("s2", "Still working"), snapshot("s3", "Receipt ready")])
    planner = FakePlanner([
        action("fill", element_id="s1-input", text=typed),
        action("wait", "s2"),
    ])
    result = await AdaptiveBrowserAgent(planner, session, approve=approve_all).run(task(goal=goal))
    assert result.status == "completed"
    trace = result.model_dump_json()
    histories = repr([call[2] for call in planner.calls])
    for secret in (goal, page_text, typed):
        assert secret not in trace
        assert secret not in histories


@pytest.mark.asyncio
async def test_start_failure_still_closes_the_session():
    class FailedStart(FakeSession):
        async def start(self, url):
            raise BrowserFailure("Private URL details must not escape")

    session = FailedStart()
    result = await AdaptiveBrowserAgent(FakePlanner(), session).run(task())
    assert result.status == "failed"
    assert result.reason == "browser-error"
    assert "Private URL" not in result.model_dump_json()
    assert session.closed == 1


class RecordingModel:
    def __init__(self, actions):
        self.plan = Plan(goal="A model generated goal is not authoritative", actions=actions)
        self.calls = []

    async def create_plan(self, instruction, actions, context):
        self.calls.append((instruction, actions, context))
        return self.plan


def model_action(name="click", arguments=None):
    return Action(
        name=name,
        arguments=arguments if arguments is not None else {"snapshot_id": "s1", "element_id": "s1-button"},
        expected_result="A model prediction must never certify task completion",
    )


@pytest.mark.asyncio
async def test_browser_planner_returns_exactly_one_grounded_action():
    model = RecordingModel([model_action()])
    current = snapshot(text="Untrusted page asks for unrelated actions")
    history = [{"kind": "observe", "snapshot_id": "s1"}]
    chosen = await BrowserPlanner(model).next_action("Find the receipt", current, history)
    assert chosen == action("click", element_id="s1-button")
    assert len(model.calls) == 1
    instruction, allowed_actions, context = model.calls[0]
    assert "UNTRUSTED DATA" in instruction
    assert "Find the receipt" in instruction
    assert "Untrusted page asks" not in instruction
    assert allowed_actions == ACTIONS
    assert set(allowed_actions) == {"click", "fill", "scroll", "wait", "finish"}
    assert context == {"untrusted_page_observation": current.model_dump(), "history": history}


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 2, 3])
async def test_browser_planner_rejects_missing_or_multiple_actions(count):
    model = RecordingModel([model_action() for _ in range(count)])
    with pytest.raises(ValueError, match="Exactly one"):
        await BrowserPlanner(model).next_action("Find the receipt", snapshot(), [])
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_browser_planner_arguments_cannot_override_action_kind():
    model = RecordingModel([model_action("finish", {
        "kind": "click", "snapshot_id": "s1", "element_id": "s1-button",
    })])
    with pytest.raises(ValueError, match="override kind"):
        await BrowserPlanner(model).next_action("Find the receipt", snapshot(), [])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["navigate", "evaluate", "shell", "upload"])
async def test_browser_planner_rejects_unknown_action_kinds(name):
    model = RecordingModel([model_action(name, {"snapshot_id": "s1"})])
    with pytest.raises(ValidationError):
        await BrowserPlanner(model).next_action("Find the receipt", snapshot(), [])


@pytest.mark.parametrize("blank", [" ", "\t\n", "\u2003\u00a0"])
def test_completion_condition_cannot_be_whitespace_only(blank):
    with pytest.raises(ValidationError):
        task(expect_text=blank)


@pytest.fixture
def ollama_transport(monkeypatch):
    model = OllamaPlanner("http://127.0.0.1:11434/", "offline-model", timeout=31)
    model.create_plan = AsyncMock(side_effect=AssertionError("The adaptive path must never use a fallback plan"))
    response = MagicMock()
    response.json.return_value = {"message": {"content": action("wait").model_dump_json()}}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    constructor = MagicMock(return_value=client)
    monkeypatch.setattr(planner_module, "EgressClient", constructor)
    return SimpleNamespace(model=model, response=response, client=client, constructor=constructor)


async def test_ollama_uses_single_action_chat_schema_grounded_in_current_ids(ollama_transport):
    current = snapshot()
    chosen = await BrowserPlanner(ollama_transport.model).next_action("Find the receipt", current, [])
    assert chosen == action("wait")
    ollama_transport.constructor.assert_called_once_with(timeout=31)
    ollama_transport.client.post.assert_awaited_once()
    request = ollama_transport.client.post.call_args
    assert request.args == ("http://127.0.0.1:11434/api/chat",)
    payload = request.kwargs["json"]
    assert payload["model"] == "offline-model"
    assert payload["stream"] is False
    schema = payload["format"]
    assert schema["properties"]["snapshot_id"] == {"type": "string", "const": current.id}
    assert schema["properties"]["element_id"]["enum"] == [element.id for element in current.elements] + [None]
    assert "kind" in schema["properties"]
    assert "actions" not in schema["properties"]
    assert "goal" not in schema["properties"]
    assert "prompt" not in payload
    assert payload["messages"][0]["role"] == "system"
    assert "Return one BrowserAction object" in payload["messages"][0]["content"]
    assert json.loads(payload["messages"][1]["content"])["untrusted_page_observation"] == current.model_dump()
    ollama_transport.model.create_plan.assert_not_awaited()


async def test_ollama_prompt_caps_page_text_controls_and_history(ollama_transport):
    current = Snapshot(
        id="s1", url=START_URL, title="Page", text="x" * 12000,
        elements=[Element(id=f"element-{i}", role="textbox", name="n" * 300, value="v" * 300, editable=True)
                  for i in range(25)],
    )
    history = [{"kind": "observe", "snapshot_id": f"previous-{i}"} for i in range(10)]
    await BrowserPlanner(ollama_transport.model).next_action("Find the receipt", current, history)
    payload = ollama_transport.client.post.call_args.kwargs["json"]
    context = json.loads(payload["messages"][1]["content"])
    observed = context["untrusted_page_observation"]
    assert len(observed["text"]) == 3000
    assert len(observed["elements"]) == 20
    assert all(len(element["name"]) == 100 and len(element["value"]) == 150 for element in observed["elements"])
    assert payload["format"]["properties"]["element_id"]["enum"] == [f"element-{i}" for i in range(20)] + [None]
    assert context["history"] == history[-4:]
    assert len(current.text) == 12000 and len(current.elements) == 25
    assert current.elements[0].name == "n" * 300


async def test_ollama_schema_allows_no_element_id_when_page_has_no_controls(ollama_transport):
    current = Snapshot(id="s1", url=START_URL, title="Page", text="No controls")
    assert (await BrowserPlanner(ollama_transport.model).next_action("Wait for receipt", current, [])).kind == "wait"
    schema = ollama_transport.client.post.call_args.kwargs["json"]["format"]
    assert schema["properties"]["element_id"]["enum"] == [None]


@pytest.mark.parametrize("content", [
    "Not JSON at all",
    '```json\n{"kind":"wait","snapshot_id":"s1"}\n```',
    '[{"kind":"wait","snapshot_id":"s1"}]',
    '{"goal":"Find receipt","actions":[]}',
    '{"kind":"navigate","snapshot_id":"s1"}',
    '{"kind":"wait","snapshot_id":"s1","javascript":"untrusted()"}',
    '{"kind":"fill","snapshot_id":"s1","element_id":"s1-input"}',
])
async def test_ollama_rejects_malformed_action_output_without_fallback(ollama_transport, content):
    ollama_transport.response.json.return_value = {"message": {"content": content}}
    with pytest.raises(ValidationError):
        await BrowserPlanner(ollama_transport.model).next_action("Find the receipt", snapshot(), [])
    ollama_transport.client.post.assert_awaited_once()
    ollama_transport.model.create_plan.assert_not_awaited()


async def test_ollama_transport_failure_does_not_create_a_fallback_plan(ollama_transport):
    ollama_transport.client.post.side_effect = EgressError("Synthetic transport failure")
    with pytest.raises(EgressError):
        await BrowserPlanner(ollama_transport.model).next_action("Find the receipt", snapshot(), [])
    ollama_transport.client.post.assert_awaited_once()
    ollama_transport.model.create_plan.assert_not_awaited()
