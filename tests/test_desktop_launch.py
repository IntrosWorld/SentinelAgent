import asyncio

import pytest

from sentinel_agent.desktop_launch import launch_intent
from sentinel_agent.desktop_service import DesktopService


def test_launch_parser_preserves_literal_text():
    assert launch_intent("Open Notepad and write Hello world.") == ("notepad", "Hello world.")
    assert launch_intent("launch Calculator") == ("calculator", None)
    assert launch_intent("open notepad and type create report.pdf") == ("notepad", "create report.pdf")


@pytest.mark.parametrize("goal", ["open powershell", "open notepad.exe", "open notepad; calc", "open chrome"])
def test_no_arbitrary_launch(goal):
    assert launch_intent(goal) is None


def test_long_text_and_wrong_application_fail_closed():
    with pytest.raises(ValueError):
        launch_intent("open notepad and write " + "x" * 301)
    with pytest.raises(ValueError):
        launch_intent("open calculator and write hello")


async def test_declined_launch_does_not_open_app(tmp_path, monkeypatch):
    def forbidden(*args):
        pytest.fail("Launch must not happen before approval")
    monkeypatch.setattr("sentinel_agent.desktop_launch.launch_app", forbidden)
    service = DesktopService(tmp_path, lambda event: None)
    await service.dispatch("run.start", {"mode": "auto", "goal": "open Notepad"})
    await asyncio.sleep(0)
    assert service.pending
    await service.dispatch("run.approve", {"run_id": service.run_id,
        "token": service.pending[0], "approved": False})
    await service.running
    assert service.history[-1]["status"] == "cancelled"


async def test_approved_launch_needs_no_model_or_window(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr("sentinel_agent.desktop_launch.launch_app", lambda root, app: launched.append(app))
    service = DesktopService(tmp_path, lambda event: None)
    await service.dispatch("run.start", {"mode": "auto", "goal": "open Calculator"})
    await asyncio.sleep(0)
    await service.dispatch("run.approve", {"run_id": service.run_id,
        "token": service.pending[0], "approved": True})
    await service.running
    assert launched == ["calculator"]
    assert service.history[-1]["status"] == "completed"
