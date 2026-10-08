"""Opt-in terminal entry point for grounded browser tasks."""

import argparse
import asyncio
from contextlib import suppress
import json
import sys
import threading
from urllib.parse import urlsplit

from sentinel_agent.computer_use.agent import AdaptiveBrowserAgent
from sentinel_agent.computer_use.browser import BrowserSession
from sentinel_agent.computer_use.planner import BrowserPlanner
from sentinel_agent.computer_use.schema import BrowserTask
from sentinel_agent.config import get_settings
from sentinel_agent.desktop import DesktopEnvironment
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.config import is_local_host, parse_destination
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.llm import planner_from_profile
from sentinel_agent.models import ModelRegistry


def build_parser():
    parser = argparse.ArgumentParser(prog="sentinel-agent browse", description="Experimental observation-driven browser agent")
    parser.add_argument("task", help="Goal for the browser agent")
    parser.add_argument("--url", required=True, help="Initial URL; existing egress rules still apply")
    parser.add_argument("--expect-text", required=True, help="Text that must actually appear in rendered page text")
    parser.add_argument("--expect-url", help="Optional exact final URL required for success")
    parser.add_argument("--model-profile", help="Existing model profile; no silent model fallback")
    parser.add_argument("--browser", choices=["chrome", "edge"], default="chrome")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-steps", type=int, default=10)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--allow-cloud-observations", action="store_true",
                        help="Explicitly permit sending extracted page text to the selected cloud model (DLP still applies)")
    parser.add_argument("--json", action="store_true", help="Noninteractive: deny click/fill and new destination approval")
    return parser


async def confirm(message, interactive):
    if not interactive:
        return False
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def read():
        try:
            accepted = input(message + " [y/N] ").strip().lower() in {"y", "yes"}
        except (EOFError, KeyboardInterrupt):
            accepted = False

        def deliver():
            if not answer.done():
                answer.set_result(accepted)
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver)

    # A cancelled run never waits for a blocked stdin worker during shutdown.
    threading.Thread(target=read, daemon=True).start()
    return await answer


async def run(args):
    gateway = None
    interactive = not args.json and sys.stdin.isatty()
    try:
        task = BrowserTask(goal=args.task, url=args.url, expect_text=args.expect_text,
                           expect_url=args.expect_url, max_steps=args.max_steps, max_seconds=args.max_seconds)
        parse_destination(task.url)
        if task.expect_url is not None:
            parse_destination(task.expect_url)
        profile = ModelRegistry(get_settings().models_file).get(args.model_profile)
        host, _, _ = parse_destination(profile.base_url)
        if not is_local_host(host) and not args.allow_cloud_observations:
            raise ValueError("Cloud page observations require --allow-cloud-observations")
        executable = DesktopEnvironment.app_commands[args.browser]
        if not executable.exists():
            raise ValueError("Selected browser executable was not found")
        # This experimental mode cannot turn off policy and silently go direct.
        gateway = EgressGateway(enabled=True, proxy_limits=ProxyLimits(
            idle_timeout=max(30, min(profile.timeout, task.max_seconds)),
        )).start()
        decision = await gateway.check(task.url)
        if not decision.allowed:
            approved = decision.approval_required and await confirm(
                f"Allow destination {json.dumps(decision.host)}:{decision.port} for this run?", interactive)
            if not approved or not gateway.approve_sync(decision):
                raise ValueError("Initial destination blocked by policy")

        async def approval(action, snapshot):
            target = next(item for item in snapshot.elements if item.id == action.element_id)
            # Escape page-supplied labels, do not let them print terminal controls.
            label = json.dumps(target.name, ensure_ascii=True)
            origin = json.dumps(urlsplit(snapshot.url).netloc, ensure_ascii=True)
            if action.kind == "fill":
                scan = gateway.scanner.protect(action.text, "approved-site")
                if not scan.allowed:
                    return False
                details = "fill with " + json.dumps(scan.body, ensure_ascii=True)
            else:
                details = "click (may submit or change data)"
            return await confirm(f"At {origin}, approve {details} on observed control {label}?", interactive)

        agent = AdaptiveBrowserAgent(BrowserPlanner(planner_from_profile(profile)),
                                     BrowserSession(gateway, executable, headless=args.headless), approval)
        if not args.json:
            print("Adaptive browser: Ctrl+C cancels. Click/fill require approval; page text is untrusted.")
        with gateway.activate():
            result = await agent.run(task)
        output = {**result.model_dump(), "egress_log": str(gateway.audit.path)}
        if args.json:
            print(json.dumps(output, indent=2))
        else:
            for event in result.trace:
                print("  " + json.dumps(event, ensure_ascii=True))
            print(f"{result.status}: {result.reason}; {result.steps} actions")
            print(f"Egress audit: {gateway.audit.path}")
        return 0 if result.status == "completed" else 1
    except (KeyError, ValueError) as exc:
        # Validation details could echo secret inputs; keep error class only.
        if args.json:
            print(json.dumps({"status": "failed", "reason": "configuration-or-destination", "error_type": type(exc).__name__}))
        else:
            print("Cannot start: check URL policy, model profile, browser installation, task limits and cloud-observation opt-in.", file=sys.stderr)
        return 2
    finally:
        if gateway:
            gateway.close()
