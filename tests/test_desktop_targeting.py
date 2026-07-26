from pathlib import Path
from unittest.mock import patch

from sentinel_agent.desktop import DesktopEnvironment
from sentinel_agent.schemas import Action


def test_live_notepad_launch_creates_unique_scratch_target(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    environment = DesktopEnvironment(live_desktop=True)
    with patch("sentinel_agent.desktop.subprocess.Popen") as popen:
        observation = environment.execute(
            Action(
                name="open_app",
                arguments={"app": "notepad"},
                expected_result="Notepad opens",
            )
        )
    assert observation.success
    scratch = Path(environment.state["scratch_file"])
    assert scratch.exists()
    assert environment.state["last_window_hint"] == scratch.name
    assert popen.call_args.args[0][1] == str(scratch)


def test_implicit_window_hint_only_applies_to_same_app():
    environment = DesktopEnvironment()
    environment.state.update(
        {"last_opened_app": "notepad", "last_window_hint": "sentinel_scratch_123.txt"}
    )
    assert environment._window_hint({"app": "notepad"}) == "sentinel_scratch_123.txt"
    assert environment._window_hint({"app": "chrome"}) is None
