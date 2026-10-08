import json
from types import SimpleNamespace

import pytest

from sentinel_agent.computer_use import desktop_cli as cli
from sentinel_agent.computer_use.desktop_schema import DesktopAction, DesktopResult


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
    monkeypatch.setattr(cli, "ModelRegistry", lambda _: SimpleNamespace(get=lambda name: SimpleNamespace(timeout=120)))
    monkeypatch.setattr(cli, "DesktopPlanner", lambda *args, **kwargs: object())
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


@pytest.mark.asyncio
async def test_replay_never_constructs_desktop(dependencies, monkeypatch, tmp_path, capsys):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"id": "s", "window_id": "12", "app": "demo", "title": "demo",
                               "width": 100, "height": 80, "elements": []}), encoding="utf-8")
    class Planner:
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
