"""Real local-model/native-input/independent-readback test in an owned window."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from uuid import uuid4

from sentinel_agent.desktop_service import DesktopService


async def run():
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
        diagnostic = WindowsDesktopSession(window_id, capture_images=False)
        await diagnostic.start()
        await diagnostic.observe()
        await diagnostic.close()
        events = []
        service = DesktopService(root, events.append)
        await service.dispatch("models.save", {
            "name":"native-smoke", "provider":"ollama", "model":"qwen2.5-coder:7b",
            "base_url":"http://127.0.0.1:11434", "vision":False, "timeout":120.0})
        expected = "Sentinel native input verified"
        await service.dispatch("run.start", {
            "goal": 'Type exactly "' + expected + '" into the editable Test input field.',
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
        evidence = {"model":"qwen2.5-coder:7b", "fixture":"real Win32 EDIT control",
                    "approval_events":sum(e["event"] == "approval" for e in events),
                    "result":result}
        (root / "native-result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps(evidence, indent=2))
        print("Evidence:", root)
        assert result["status"] == "completed" and result["reason"] == "goal-verified", result
    finally:
        fixture.terminate()
        fixture.wait(timeout=10)


if __name__ == "__main__":
    asyncio.run(run())
