"""Run a real local vision model on a saved window, without sending any input.

This measures perception/planning only. It is deliberately separate from the
native executor so a model's proposed coordinates cannot become an auto-click.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from sentinel_agent.computer_use.desktop_planner import DesktopPlanner
from sentinel_agent.computer_use.desktop_schema import DesktopSnapshot
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.models import ModelProfile


async def probe(snapshot_path, goal, model, timeout):
    raw = snapshot_path.read_bytes()
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Snapshot too large")
    snapshot = DesktopSnapshot.model_validate_json(raw)
    profile = ModelProfile(name="desktop-probe", provider="ollama", model=model,
                           base_url="http://127.0.0.1:11434", timeout=timeout, vision=True)
    gateway = EgressGateway(enabled=True, proxy_limits=ProxyLimits(idle_timeout=timeout)).start()
    started = time.monotonic()
    result = {"mode": "proposal_only_no_input", "model": model,
              "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
              "snapshot_id": snapshot.id, "window_size": [snapshot.width, snapshot.height],
              "element_count": len(snapshot.elements), "goal": goal}
    planner = DesktopPlanner(profile)
    try:
        with gateway.activate():
            action = await asyncio.wait_for(planner.next_action(goal, snapshot, []), timeout=timeout)
        result.update(status="proposed", action=action.model_dump(exclude_none=True))
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
    finally:
        result["seconds"] = round(time.monotonic() - started, 2)
        result["model_metrics"] = planner.last_metrics
        result["egress_log"] = str(gateway.audit.path)
        gateway.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("goal")
    parser.add_argument("--model", default="qwen3-vl:2b-instruct")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 900:
        parser.error("timeout must be between 1 and 900 seconds")
    result = asyncio.run(probe(args.snapshot, args.goal, args.model, args.timeout))
    encoded = json.dumps(result, indent=2, ensure_ascii=True)
    if args.output:
        # Do not overwrite previous evidence accidentally.
        with args.output.open("x", encoding="utf-8") as destination:
            destination.write(encoded + "\n")
    print(encoded)
    return 0 if result["status"] == "proposed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
