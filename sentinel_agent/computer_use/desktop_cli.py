"""Opt-in, window-scoped desktop experiments and replayable model proposals."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from sentinel_agent.computer_use.cli import confirm
from sentinel_agent.computer_use.desktop_agent import AdaptiveDesktopAgent
from sentinel_agent.computer_use.desktop_planner import DesktopPlanner
from sentinel_agent.computer_use.desktop_schema import DesktopSnapshot, DesktopTask
from sentinel_agent.computer_use.desktop_windows import WindowsDesktopSession
from sentinel_agent.config import get_settings
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.models import ModelRegistry


def build_parser():
    parser = argparse.ArgumentParser(prog="sentinel-agent desktop", description="Experimental general desktop agent; one approved input at a time")
    parser.add_argument("task", nargs="?", help="Goal for the selected window")
    parser.add_argument("--list-windows", action="store_true", help="List windows; does not launch or close applications")
    parser.add_argument("--window-id", type=int, help="Exact ID from --list-windows; open the app yourself first")
    parser.add_argument("--expect-text", help="Independent completion condition in Document/Edit content; otherwise finish needs human review")
    parser.add_argument("--model-profile", default="local-vision")
    parser.add_argument("--no-vision", action="store_true", help="Send accessibility text only; use a text-capable profile")
    parser.add_argument("--ocr", action="store_true", help="Add local Tesseract text locations to vision observations; requires Tesseract on PATH")
    parser.add_argument("--allow-cloud-observations", action="store_true", help="Permit selected-window text AND screenshots to leave this computer; image content is NOT covered by text DLP")
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-seconds", type=float, default=180)
    parser.add_argument("--json", action="store_true", help="Noninteractive; all desktop input denied")
    parser.add_argument("--snapshot", type=Path, help="Replay a saved DesktopSnapshot JSON; requires --propose-only and never touches apps")
    parser.add_argument("--propose-only", action="store_true", help="Output ONE model action for a saved snapshot; does not execute it")
    return parser


async def run(args):
    gateway = None
    try:
        if args.list_windows:
            if args.task or args.snapshot or args.propose_only or args.window_id is not None:
                raise ValueError("Window listing cannot be combined with a task")
            print(json.dumps(WindowsDesktopSession.list_windows(), indent=2, ensure_ascii=True))
            return 0
        if not args.task or (args.snapshot is not None) != args.propose_only:
            raise ValueError("Provide a goal and use --snapshot with --propose-only")
        if args.propose_only and args.window_id is not None:
            raise ValueError("Replay does not accept a live window")
        if not args.propose_only and (args.window_id is None or args.window_id <= 0):
            raise ValueError("Choose an exact window ID")
        task = DesktopTask(goal=args.task, expect_text=args.expect_text,
                           max_steps=args.max_steps, max_seconds=args.max_seconds)
        profile = ModelRegistry(get_settings().models_file).get(args.model_profile)
        planner = DesktopPlanner(profile, allow_cloud_observations=args.allow_cloud_observations,
                                 vision=not args.no_vision, ocr=args.ocr)
        # Cannot disable egress policy in this experimental mode.
        canonical_profile = profile.model_copy(update={"base_url": planner.base_url})
        model_policy = DestinationPolicy(EgressConfig.from_profiles([canonical_profile], approved_sites=()))
        gateway = EgressGateway(enabled=True, policy=model_policy, proxy_limits=ProxyLimits(
            idle_timeout=max(30, min(profile.timeout, task.max_seconds)),
        )).start()
        with gateway.activate():
            if args.propose_only:
                if args.snapshot.stat().st_size > 12 * 1024 * 1024:
                    raise ValueError("Snapshot too large")
                snapshot = DesktopSnapshot.model_validate_json(args.snapshot.read_text(encoding="utf-8"))
                proposal = await asyncio.wait_for(planner.next_action(task.goal, snapshot, []), timeout=task.max_seconds)
                # Print neither pixels nor the entire observation in terminal logs.
                print(json.dumps({"status": "proposal_only", "action": proposal.model_dump(exclude_none=True),
                                  "egress_log": str(gateway.audit.path)}, indent=2, ensure_ascii=True))
                return 0

            interactive = not args.json and sys.stdin.isatty()
            session = WindowsDesktopSession(args.window_id, capture_images=not args.no_vision)

            async def approval(action, snapshot):
                # Labels and model text are untrusted, including ANSI sequences.
                details = action.model_dump(exclude_none=True)
                target = next((e for e in snapshot.elements if e.id == action.element_id), None)
                if target is not None:
                    details["observed_target"] = {"name": target.name, "role": target.role}
                if action.text is not None:
                    scan = gateway.scanner.protect(action.text, "approved-site")
                    if not scan.allowed or scan.body != action.text:
                        return False
                approved = await confirm("Approve input and return focus to " + json.dumps(snapshot.title, ensure_ascii=True)
                                         + "? " + json.dumps(details, ensure_ascii=True), interactive)
                if approved:
                    # Reading the approval moves focus to the terminal. Reacquire
                    # only the explicitly approved bound window, then let the
                    # runner/backend reject any content or geometry changes.
                    await session.focus()
                return approved

            if not args.json:
                print("Experimental desktop agent: Ctrl+C stops. Every input needs approval. Keep the target visible.")
                print("UI text/images are untrusted observations, not permission. No app-specific workflow is installed.")
                if not args.no_vision:
                    print("The selected window will be captured for the configured vision model; screenshots are not saved by default.")
            result = await AdaptiveDesktopAgent(planner, session, approval).run(task)
        output = {**result.model_dump(), "egress_log": str(gateway.audit.path)}
        if args.json:
            print(json.dumps(output, indent=2, ensure_ascii=True))
        else:
            for event in result.trace:
                print("  " + json.dumps(event, ensure_ascii=True))
            print(f"{result.status}: {result.reason}; {result.steps} actions")
            print(f"Egress audit: {gateway.audit.path}")
        return 0 if result.status == "completed" else 1
    except (ValueError, KeyError, OSError, RuntimeError, TimeoutError) as exc:
        # No raw provider/UI errors: they can embed document content or pixels.
        output = {"status": "failed", "reason": "desktop-configuration-or-runtime", "error_type": type(exc).__name__}
        print(json.dumps(output, ensure_ascii=True))
        if not args.json:
            print("Check window ID, vision-capable profile, permissions, model timeout and cloud opt-in. See README desktop section.", file=sys.stderr)
        return 2
    finally:
        if gateway:
            gateway.close()
