import json
from types import SimpleNamespace

import pytest

from sentinel_agent.computer_use import desktop_cli as cli
from sentinel_agent.computer_use.desktop_schema import DesktopAction, DesktopResult
from sentinel_agent.models import ModelProfile


class FakeGateway:
    enabled = True
    audit = SimpleNamespace(path="test-audit.jsonl")
    scanner = SimpleNamespace(protect=lambda text, category: SimpleNamespace(allowed=True, body=text))

    def start(self):
        return self

    def activate(self):
        from contextlib import nullcontext
        return nullcontext()

    def close(self):
        self.closed = True


@pytest.fixture
def dependencies(monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(cli, "EgressGateway", lambda **kwargs: gateway)
    profile = ModelProfile(name="test", provider="ollama", model="local", base_url="http://127.0.0.1:11434", vision=True)
    monkeypatch.setattr(cli, "ModelRegistry", lambda _: SimpleNamespace(get=lambda name: profile))
    monkeypatch.setattr(cli, "DesktopPlanner", lambda *args, **kwargs: SimpleNamespace(base_url=profile.base_url))
    monkeypatch.setattr(cli, "WindowsDesktopSession", lambda *args, **kwargs: object())
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [[], ["test goal"], ["test goal", "--window-id", "0"],
    ["test goal", "--snapshot", "x.json"], ["test goal", "--propose-only"],
    ["test goal", "--snapshot", "x.json", "--propose-only", "--window-id", "1"]])
async def test_invalid_invocation_never_starts_desktop(argv, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Desktop must not be constructed")
    monkeypatch.setattr(cli, "WindowsDesktopSession", forbidden)
    assert await cli.run(cli.build_parser().parse_args(argv)) == 2


@pytest.mark.asyncio
async def test_listing_is_read_only(monkeypatch, capsys):
    monkeypatch.setattr(cli, "WindowsDesktopSession", SimpleNamespace(list_windows=lambda: [{"window_id": 12, "title": "demo"}]))
    assert await cli.run(cli.build_parser().parse_args(["--list-windows"])) == 0
    assert json.loads(capsys.readouterr().out)[0]["window_id"] == 12


@pytest.mark.asyncio
async def test_json_denies_input_and_closes_gateway(dependencies, monkeypatch, capsys):
    class Agent:
        def __init__(self, planner, session, approve):
            self.approve = approve

        async def run(self, task):
            action = DesktopAction(kind="click", snapshot_id="s", x=2, y=3)
            snapshot = SimpleNamespace(title="demo\x1b[2J", elements=[])
            assert await self.approve(action, snapshot) is False
            return DesktopResult(status="failed", reason="approval-denied", steps=0)
    monkeypatch.setattr(cli, "AdaptiveDesktopAgent", Agent)
    assert await cli.run(cli.build_parser().parse_args(["test goal", "--window-id", "12", "--json"])) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "approval-denied"
    assert dependencies.closed


async def test_single_step_reaches_runner_without_relaxing_json_approval(dependencies, monkeypatch, capsys):
    class Agent:
        def __init__(self, planner, session, approve):
            self.approve = approve
        async def run(self, task):
            assert task.single_step is True
            assert await self.approve(DesktopAction(kind="click", snapshot_id="s", x=1, y=1),
                                      SimpleNamespace(title="fixture", elements=[])) is False
            return DesktopResult(status="failed", reason="approval-denied", steps=0)
    monkeypatch.setattr(cli, "AdaptiveDesktopAgent", Agent)
    args = cli.build_parser().parse_args(["test goal", "--window-id", "12", "--json", "--single-step"])
    assert await cli.run(args) == 1
    assert json.loads(capsys.readouterr().out)["steps"] == 0


@pytest.mark.asyncio
async def test_replay_never_constructs_desktop(dependencies, monkeypatch, tmp_path, capsys):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"id": "s", "window_id": "12", "app": "demo", "title": "demo",
                               "width": 100, "height": 80, "elements": []}), encoding="utf-8")
    class Planner:
        base_url = "http://127.0.0.1:11434"
        async def next_action(self, *args):
            return DesktopAction(kind="click", snapshot_id="s", x=20, y=30)
    monkeypatch.setattr(cli, "DesktopPlanner", lambda *args, **kwargs: Planner())
    def forbidden(*args, **kwargs):
        pytest.fail("Replay must not construct a desktop session")
    monkeypatch.setattr(cli, "WindowsDesktopSession", forbidden)
    args = cli.build_parser().parse_args(["test goal", "--snapshot", str(path), "--propose-only", "--json"])
    assert await cli.run(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "proposal_only"
    assert "screenshot" not in result
    assert dependencies.closed


@pytest.mark.asyncio
async def test_approved_input_reacquires_only_bound_window(dependencies, monkeypatch, capsys):
    events = []
    class Session:
        async def focus(self):
            events.append("focus")
    monkeypatch.setattr(cli, "WindowsDesktopSession", lambda *args, **kwargs: Session())
    async def approve(message, interactive):
        events.append("approve")
        assert "\\u001b" in message
        return True
    monkeypatch.setattr(cli, "confirm", approve)
    class Agent:
        def __init__(self, planner, session, approve):
            self.approve = approve
        async def run(self, task):
            action = DesktopAction(kind="click", snapshot_id="s", x=2, y=3)
            assert await self.approve(action, SimpleNamespace(title="demo\x1b[2J", elements=[])) is True
            assert events == ["approve", "focus"]
            return DesktopResult(status="needs_review", reason="visual-verification-required", steps=1)
    monkeypatch.setattr(cli, "AdaptiveDesktopAgent", Agent)
    assert await cli.run(cli.build_parser().parse_args(["test goal", "--window-id", "12"])) == 1
    assert "needs_review" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_ocr_flag_reaches_planner_in_replay(dependencies, monkeypatch, tmp_path):
    path = tmp_path / "frame.json"
    path.write_text(json.dumps({"id": "s", "window_id": "1", "app": "fixture", "title": "fixture",
                               "width": 100, "height": 80}), encoding="utf-8")
    class Planner:
        base_url = "http://127.0.0.1:11434"
        async def next_action(self, *args):
            return DesktopAction(kind="click", snapshot_id="s", x=20, y=30)
    def build(*args, **kwargs):
        assert kwargs["ocr"] is True
        assert kwargs["vision"] is True
        return Planner()
    monkeypatch.setattr(cli, "DesktopPlanner", build)
    args = cli.build_parser().parse_args(["Click test", "--snapshot", str(path), "--propose-only", "--ocr"])
    assert await cli.run(args) == 0


@pytest.mark.asyncio
async def test_model_cli_saves_explicit_coordinate_mode(tmp_path, monkeypatch):
    from sentinel_agent import cli as main_cli
    from sentinel_agent.models import ModelRegistry
    registry_path = tmp_path / "models.json"
    monkeypatch.setattr(main_cli, "get_settings", lambda: SimpleNamespace(models_file=str(registry_path)))
    args = main_cli.build_models_parser().parse_args([
        "add", "local-alias", "--provider", "ollama", "--model", "my-vision-model",
        "--base-url", "http://127.0.0.1:11434", "--vision", "--desktop-coordinates", "normalized_1000",
    ])
    assert await main_cli.manage_models(args) == 0
    saved = ModelRegistry(registry_path).get("local-alias")
    assert saved.desktop_coordinates == "normalized_1000"
    assert saved.vision is True
    assert ModelRegistry(registry_path).get().name == "local-ollama"


@pytest.mark.asyncio
async def test_selected_localhost_profile_uses_canonical_exact_policy(dependencies, monkeypatch, tmp_path):
    received = {}
    profile = ModelProfile(name="test", provider="ollama", model="local", base_url="http://localhost:11435", vision=True)
    monkeypatch.setattr(cli, "ModelRegistry", lambda _: SimpleNamespace(get=lambda name: profile))
    def gateway(**kwargs):
        received.update(kwargs)
        return dependencies
    monkeypatch.setattr(cli, "EgressGateway", gateway)
    class Planner:
        base_url = "http://127.0.0.1:11435"
        async def next_action(self, *args):
            return DesktopAction(kind="wait", snapshot_id="s")
    monkeypatch.setattr(cli, "DesktopPlanner", lambda *args, **kwargs: Planner())
    path = tmp_path / "frame.json"
    path.write_text(json.dumps({"id": "s", "window_id": "1", "app": "fixture", "title": "fixture",
                               "width": 100, "height": 80}), encoding="utf-8")
    args = cli.build_parser().parse_args(["Inspect test", "--snapshot", str(path), "--propose-only"])
    assert await cli.run(args) == 0
    assert (await received["policy"].evaluate_url("http://127.0.0.1:11435/api/chat")).allowed
    assert not (await received["policy"].evaluate_url("http://127.0.0.1:11434/api/chat")).allowed
