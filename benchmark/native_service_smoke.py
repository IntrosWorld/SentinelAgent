"""Real local-model/native-input/independent-readback test in an owned window."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from sentinel_agent.desktop_service import DesktopService


async def run(model="qwen3:1.7b", scenario="type"):
    import ctypes
    from ctypes import wintypes
    import psutil
    user = ctypes.WinDLL("user32")
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    foreground_pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(user.GetForegroundWindow(), ctypes.byref(foreground_pid))
    if not foreground_pid.value or psutil.Process(foreground_pid.value).name().casefold() in {"lockapp.exe", "logonui.exe"}:
        raise RuntimeError("Unlock Windows before running the native-input smoke test")
    root = Path(tempfile.mkdtemp(prefix="sentinel-native-"))
    fixture = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("native_input_fixture.py")), "Sentinel input fixture " + uuid4().hex[:8]],
        stdout=subprocess.PIPE, text=True)
    try:
        window_id = int(await asyncio.wait_for(asyncio.to_thread(fixture.stdout.readline), 15))
        from sentinel_agent.computer_use.desktop_windows import WindowsDesktopSession
        import sentinel_agent.desktop_service as service_module
        class InstrumentedSession(WindowsDesktopSession):
            async def focus(self):
                try:
                    return await super().focus()
                except Exception as exc:
                    print("Native focus:", str(exc), flush=True)
                    raise
            async def observe(self):
                snapshot = await super().observe()
                (root / "last-observation.json").write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
                return snapshot
            async def validate(self, action):
                try:
                    return await super().validate(action)
                except Exception as exc:
                    print("Native validation:", str(exc), flush=True)
                    raise
        service_module.WindowsDesktopSession = InstrumentedSession
        diagnostic = WindowsDesktopSession(window_id, capture_images=False)
        await diagnostic.start()
        await diagnostic.observe()
        await diagnostic.close()
        events = []
        service = DesktopService(root, events.append)
        await service.dispatch("models.save", {
            "name":"native-smoke", "provider":"ollama", "model":model,
            "base_url":"http://127.0.0.1:11434", "vision":False, "timeout":120.0})
        scenarios = {
            "type": ('Type exactly "Sentinel native input verified" into the editable Test input field.', "Sentinel native input verified"),
            "click": ('Click the Insert greeting button once.', "Button click verified"),
            "keyboard": ('Press the ENTER key once.', "Keyboard input verified"),
        }
        goal, expected = scenarios[scenario]
        await service.dispatch("run.start", {
            "goal": goal,
            "mode":"desktop", "window_id":window_id, "profile":"native-smoke",
            "vision":False, "single_step":False, "expect_text":expected, "max_seconds":180.0})
        async def drive():
            while not service.running.done():
                if service.pending and not service.pending[1].done():
                    await service.dispatch("run.approve", {
                        "run_id":service.run_id, "token":service.pending[0], "approved":True})
                await asyncio.sleep(.05)
            await service.running
        await asyncio.wait_for(drive(), 200)
        result = next(e["result"] for e in events if e["event"] == "finished")
        evidence = {"model":model, "scenario":scenario, "fixture":"real Win32 controls",
                    "approval_events":sum(e["event"] == "approval" for e in events),
                    "result":result}
        (root / "native-result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
        print("Evidence:", root)
        assert result["status"] == "completed" and result["reason"] == "goal-verified", result
        required_action = {"type":"type", "click":"click", "keyboard":"press_key"}[scenario]
        assert any(item.get("kind") == "action" and item.get("action") == required_action
                   for item in result["trace"]), "The requested native action was not dispatched"
    finally:
        fixture.terminate()
        fixture.wait(timeout=10)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--scenario", choices=["type", "click", "keyboard"], default="type")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.model, arguments.scenario))
