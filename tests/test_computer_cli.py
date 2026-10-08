"""Experimental browser CLI contracts, with no real network or UI access."""

from contextlib import nullcontext
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel_agent.computer_use import cli
from sentinel_agent.computer_use.schema import Element, Snapshot
from sentinel_agent.egress.dlp import DLPScanner
from sentinel_agent.models import ModelProfile
from sentinel_agent.schemas import Action, Plan


def arguments(*extra):
    return cli.build_parser().parse_args([
        "Find the receipt", "--url", "https://example.test/start", "--expect-text", "Receipt ready", *extra,
    ])


@pytest.mark.parametrize("missing", ["--url", "--expect-text"])
def test_browser_cli_requires_start_url_and_completion_text(missing, capsys):
    values = ["Find the receipt"]
    if missing != "--url":
        values += ["--url", "https://example.test"]
    if missing != "--expect-text":
        values += ["--expect-text", "Receipt ready"]
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(values)
    assert exc.value.code == 2
    assert missing in capsys.readouterr().err


def test_browser_cli_exposes_limits_and_explicit_cloud_opt_in():
    args = arguments("--max-steps", "3", "--max-seconds", "20", "--browser", "edge", "--headless",
                     "--allow-cloud-observations", "--expect-url", "https://example.test/done")
    assert args.max_steps == 3
    assert args.max_seconds == 20
    assert args.allow_cloud_observations
    assert args.expect_url == "https://example.test/done"
    assert args.browser == "edge" and args.headless


def test_browser_cli_does_not_offer_an_egress_bypass(capsys):
    with pytest.raises(SystemExit) as exc:
        arguments("--egress", "off")
    assert exc.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


async def test_noninteractive_confirmation_declines_without_reading_stdin(monkeypatch):
    prompt = MagicMock(side_effect=AssertionError("stdin must not be read"))
    monkeypatch.setattr("builtins.input", prompt)
    assert await cli.confirm("Approve this action?", interactive=False) is False
    prompt.assert_not_called()


async def test_cloud_observations_require_opt_in_before_gateway_start(monkeypatch, capsys):
    profile = ModelProfile(name="cloud", provider="openai_compatible", model="test-model",
                           base_url="https://model.example.test/v1")
    monkeypatch.setattr(cli.ModelRegistry, "get", lambda self, name=None: profile)
    gateway = MagicMock(side_effect=AssertionError("Cloud opt-in must be checked first"))
    monkeypatch.setattr(cli, "EgressGateway", gateway)
    args = arguments("--json", "--model-profile", "cloud")
    assert await cli.run(args) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["reason"] == "configuration-or-destination"
    gateway.assert_not_called()


@pytest.fixture
def fake_runtime(monkeypatch, tmp_path):
    profile = ModelProfile(name="local", provider="ollama", model="test-model", base_url="http://127.0.0.1:11434")
    monkeypatch.setattr(cli.ModelRegistry, "get", lambda self, name=None: profile)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    executable = MagicMock()
    executable.exists.return_value = True
    monkeypatch.setattr(cli.DesktopEnvironment, "app_commands", {"chrome": executable, "edge": executable})
    gateway = MagicMock()
    gateway.start.return_value = gateway
    gateway.check = AsyncMock(return_value=SimpleNamespace(allowed=True))
    gateway.activate.return_value = nullcontext()
    gateway.audit.path = tmp_path / "fake-audit.jsonl"
    gateway.scanner = DLPScanner()
    constructor = MagicMock(return_value=gateway)
    monkeypatch.setattr(cli, "EgressGateway", constructor)
    model = SimpleNamespace(create_plan=AsyncMock())
    monkeypatch.setattr(cli, "planner_from_profile", lambda selected: model)
    session = SimpleNamespace(start=AsyncMock(), observe=AsyncMock(), act=AsyncMock(), close=AsyncMock())
    session_constructor = MagicMock(return_value=session)
    monkeypatch.setattr(cli, "BrowserSession", session_constructor)
    return SimpleNamespace(gateway=gateway, gateway_constructor=constructor, model=model,
                           session=session, session_constructor=session_constructor, executable=executable)


async def test_cli_runs_real_adaptive_loop_with_mocked_browser(fake_runtime, capsys):
    fake_runtime.session.observe.return_value = Snapshot(
        id="s1", url="https://example.test/start", title="Test", text="Receipt ready",
    )
    args = arguments("--json", "--headless", "--max-steps", "2")
    assert await cli.run(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "completed"
    assert output["reason"] == "goal-verified"
    assert output["steps"] == 0
    assert fake_runtime.gateway_constructor.call_args.kwargs["enabled"] is True
    assert fake_runtime.gateway_constructor.call_args.kwargs["proxy_limits"].idle_timeout == 120
    fake_runtime.session_constructor.assert_called_once_with(fake_runtime.gateway, fake_runtime.executable, headless=True)
    fake_runtime.session.start.assert_awaited_once_with("https://example.test/start")
    fake_runtime.session.close.assert_awaited_once()
    fake_runtime.model.create_plan.assert_not_awaited()
    fake_runtime.gateway.close.assert_called_once()


@pytest.mark.parametrize("kind", ["click", "fill"])
async def test_json_mode_denies_state_changing_actions_even_in_a_terminal(fake_runtime, monkeypatch, capsys, kind):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    fake_runtime.session.observe.return_value = Snapshot(
        id="s1", url="https://example.test/start", title="Test", text="Not complete",
        elements=[Element(id="e1", role="textbox", name="Query", editable=True)],
    )
    action_arguments = {"snapshot_id": "s1", "element_id": "e1"}
    if kind == "fill":
        action_arguments["text"] = "hello world"
    fake_runtime.model.create_plan.return_value = Plan(goal="Find the receipt", actions=[
        Action(name=kind, arguments=action_arguments, expected_result="Maybe a result"),
    ])
    prompt = MagicMock(side_effect=AssertionError("JSON mode must not request human input"))
    monkeypatch.setattr("builtins.input", prompt)
    assert await cli.run(arguments("--json")) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "approval-denied"
    fake_runtime.session.act.assert_not_awaited()
    fake_runtime.session.close.assert_awaited_once()
    fake_runtime.gateway.close.assert_called_once()
    prompt.assert_not_called()


async def test_unapproved_destination_is_not_opened_in_json_mode(fake_runtime, capsys):
    fake_runtime.gateway.check.return_value = SimpleNamespace(
        allowed=False, approval_required=True, host="new.example.test", port=443,
    )
    assert await cli.run(arguments("--json")) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "configuration-or-destination"
    fake_runtime.session_constructor.assert_not_called()
    fake_runtime.gateway.approve_sync.assert_not_called()
    fake_runtime.gateway.close.assert_called_once()


async def test_cloud_opt_in_allows_selected_profile_to_reach_runner(fake_runtime, monkeypatch, capsys):
    cloud = ModelProfile(name="cloud", provider="openai_compatible", model="test-model",
                         base_url="https://model.example.test/v1")
    monkeypatch.setattr(cli.ModelRegistry, "get", lambda self, name=None: cloud)
    planner = MagicMock(return_value=fake_runtime.model)
    monkeypatch.setattr(cli, "planner_from_profile", planner)
    fake_runtime.session.observe.return_value = Snapshot(id="s1", url="https://example.test/start", title="Test", text="Receipt ready")
    assert await cli.run(arguments("--json", "--allow-cloud-observations")) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    planner.assert_called_once_with(cloud)


async def test_validation_errors_never_echo_private_task_input(monkeypatch, capsys):
    gateway = MagicMock(side_effect=AssertionError("Invalid task must fail before startup"))
    monkeypatch.setattr(cli, "EgressGateway", gateway)
    args = arguments("--json")
    args.task = "private-task-value-" + "x" * 2000
    assert await cli.run(args) == 2
    output = capsys.readouterr().out
    assert "private-task-value" not in output
    assert json.loads(output)["reason"] == "configuration-or-destination"
    gateway.assert_not_called()


async def test_approval_preview_shows_escaped_origin_target_and_redacted_proposed_text(fake_runtime, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    label = 'Query "quoted"\x1b[31m\nnext line'
    proposed = 'Contact person@example.com\nSay "hello"'
    fake_runtime.session.observe.return_value = Snapshot(
        id="s1", url="https://example.test:8443/page?private=query", title="Test", text="Not complete",
        elements=[Element(id="e1", role="textbox", name=label, editable=True)],
    )
    fake_runtime.model.create_plan.return_value = Plan(goal="Find the receipt", actions=[Action(
        name="fill", arguments={"snapshot_id": "s1", "element_id": "e1", "text": proposed}, expected_result="Maybe a result",
    )])
    confirm = AsyncMock(return_value=False)
    monkeypatch.setattr(cli, "confirm", confirm)
    assert await cli.run(arguments()) == 1
    confirm.assert_awaited_once()
    message, interactive = confirm.call_args.args
    assert interactive is True
    assert 'At "example.test:8443"' in message
    assert json.dumps(label, ensure_ascii=True) in message
    assert json.dumps('Contact [REDACTED:email]\nSay "hello"', ensure_ascii=True) in message
    assert "person@example.com" not in message
    assert "private=query" not in message
    assert "\x1b" not in message and "\n" not in message
    fake_runtime.session.act.assert_not_awaited()
    fake_runtime.session.close.assert_awaited_once()
    assert "person@example.com" not in capsys.readouterr().out


async def test_sensitive_fill_is_denied_before_approval_prompt(fake_runtime, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    secret = "sk-" + "a" * 30
    fake_runtime.session.observe.return_value = Snapshot(
        id="s1", url="https://example.test/start", title="Test", text="Not complete",
        elements=[Element(id="e1", role="textbox", name="Query", editable=True)],
    )
    fake_runtime.model.create_plan.return_value = Plan(goal="Find the receipt", actions=[Action(
        name="fill", arguments={"snapshot_id": "s1", "element_id": "e1", "text": secret}, expected_result="Maybe a result",
    )])
    confirm = AsyncMock(return_value=True)
    monkeypatch.setattr(cli, "confirm", confirm)
    assert await cli.run(arguments()) == 1
    confirm.assert_not_awaited()
    fake_runtime.session.act.assert_not_awaited()
    fake_runtime.session.close.assert_awaited_once()
    output = capsys.readouterr().out
    assert "approval-denied" in output
    assert secret not in output
