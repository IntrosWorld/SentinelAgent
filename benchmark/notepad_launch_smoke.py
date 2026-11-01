"""Opt-in real Notepad launch, type, save/readback and close test."""
import asyncio
import json
from pathlib import Path
import tempfile

from sentinel_agent.desktop_service import DesktopService


async def main():
    root = Path(tempfile.mkdtemp(prefix="sentinel-notepad-"))
    events = []
    service = DesktopService(root, events.append)
    await service.dispatch("run.start", {"mode": "auto",
        "goal": "Open Notepad and write Sentinel opened Notepad and typed this text.",
        "vision": False, "single_step": False})
    try:
        async with asyncio.timeout(200):
            while not service.running.done():
                if service.pending and not service.pending[1].done():
                    details = next(e["details"] for e in reversed(events) if e["event"] == "approval")
                    print("Approving owned scratch task:", details["action"], flush=True)
                    await service.dispatch("run.approve", {"run_id": service.run_id,
                        "token": service.pending[0], "approved": True})
                await asyncio.sleep(.1)
            await service.running
        result = next(e["result"] for e in events if e["event"] == "finished")
        (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2), flush=True)
        print("Evidence:", root, flush=True)
        assert result["status"] == "completed" and result["reason"] == "Notepad saved, verified and closed"
        assert Path(result["saved_file"]).read_text() == "Sentinel opened Notepad and typed this text."
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
