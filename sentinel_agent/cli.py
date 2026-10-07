from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from contextlib import suppress

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.config import get_settings
from sentinel_agent.llm import PlannerModel, planner_from_profile
from sentinel_agent.models import ModelProfile, ModelRegistry
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import AgentRun, RunStatus
from sentinel_agent.egress.telemetry import AuditLog, read_verified, report


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    LIME = "\033[38;5;154m"
    CYAN = "\033[38;5;81m"
    YELLOW = "\033[38;5;220m"
    RED = "\033[38;5;203m"
    GREY = "\033[38;5;245m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in __import__("os").environ


def paint(text: str, *styles: str) -> str:
    if not _supports_color():
        return text
    return "".join(styles) + text + Style.RESET


def banner() -> None:
    print()
    print(paint("  SENTINEL", Style.BOLD) + paint("AGENT", Style.BOLD, Style.LIME))
    print(paint("  offline | verified | human-controlled", Style.DIM))
    print()


async def spinner(task: asyncio.Task, label: str) -> None:
    frames = "|/-\\"
    index = 0
    while not task.done():
        print(f"\r  {paint(frames[index % len(frames)], Style.LIME)} {label}", end="", flush=True)
        index += 1
        await asyncio.sleep(0.12)
    print("\r" + " " * (len(label) + 8) + "\r", end="", flush=True)


def make_agent(profile_name: str | None = None, egress_enabled=None) -> tuple[SentinelAgent, PlannerModel, ModelRegistry]:
    settings = get_settings()
    registry = ModelRegistry(settings.models_file)
    profile = registry.get(profile_name)
    model = planner_from_profile(profile)
    return SentinelAgent(HybridPlanner(model), settings.max_steps, egress_enabled=egress_enabled), model, registry


def show_health(health: dict) -> None:
    dot = "*"
    label = health.get("profile") or health.get("model", "model")
    if health.get("online") and health.get("installed"):
        print(f"  {paint(dot, Style.LIME)} model  {label} ({health['model']})  {paint('ready', Style.LIME)}")
    elif health.get("online"):
        print(f"  {paint(dot, Style.YELLOW)} model  {label} ({health['model']})  {paint('not found', Style.YELLOW)}")
    else:
        error = f" ({health['error']})" if health.get("error") else ""
        print(f"  {paint(dot, Style.RED)} model  {label} unavailable{error}  {paint('fallback ready', Style.GREY)}")


def show_plan(run: AgentRun) -> None:
    if run.plan is None:
        return
    planner_event = next((event for event in run.events if event.kind == "plan"), None)
    source = planner_event.message.removeprefix("Plan created by ") if planner_event else "unknown"
    print(f"\n  {paint('PLAN', Style.BOLD, Style.CYAN)}  {paint(source, Style.GREY)}")
    for number, action in enumerate(run.plan.actions, start=1):
        detail = f" {json.dumps(action.arguments)}" if action.arguments else ""
        print(f"  {paint(str(number).rjust(2), Style.GREY)}  {action.name}{paint(detail, Style.DIM)}")


def show_trace(run: AgentRun) -> None:
    print(f"\n  {paint('VERIFIED TRACE', Style.BOLD, Style.CYAN)}")
    for event in run.events:
        if event.kind not in {"action", "approval", "complete", "egress"}:
            continue
        verified = event.data.get("verified")
        symbol = paint("OK", Style.LIME) if verified or event.kind == "complete" else paint("-", Style.YELLOW)
        action = event.data.get("action")
        prefix = f"{action}: " if action else ""
        print(f"  {symbol} {prefix}{event.message}")


def show_result(run: AgentRun) -> None:
    color = Style.LIME if run.status == RunStatus.COMPLETED else Style.RED
    print(f"\n  {paint(run.status.upper().replace('_', ' '), Style.BOLD, color)}")
    if run.result.get("shortages") is not None:
        shortages = run.result["shortages"]
        if shortages:
            print("  Attendance shortages:")
            for subject, attendance in shortages.items():
                print(f"    - {subject}: {attendance}%")
        else:
            print("  No attendance shortages found.")
    if run.result.get("report"):
        print("\n" + "\n".join(f"  {line}" for line in run.result["report"].splitlines()))
    if run.result.get("error"):
        print(f"  {paint(run.result['error'], Style.RED)}")
    if run.result.get("egress_log"):
        print(f"  Egress audit: {run.result['egress_log']}")
    if run.result.get("active_window_title"):
        print(f"  Window: {run.result['active_window_title']}")
    controls = run.result.get("observed_controls")
    if controls:
        print("  Accessible controls:")
        for control in controls[:20]:
            print(f"    - [{control['type']}] {control['name']}")
        if len(controls) > 20:
            print(f"    ... and {len(controls) - 20} more")
    if run.result.get("scratch_file"):
        print(f"  Scratch file: {run.result['scratch_file']}")
    if run.result.get("file"):
        file_result = run.result["file"]
        print(f"  File: {file_result.get('output_path')}")
        if file_result.get("backup_path"):
            print(f"  Backup: {file_result['backup_path']}")
        render_ok = file_result.get("render_ok")
        if render_ok is not None:
            print(f"  Visual render: {'passed' if render_ok else 'unavailable/failed'}")


async def execute(
    agent: SentinelAgent,
    instruction: str,
    use_model: bool,
    disturbance: str | None,
    live_desktop: bool = False,
    json_output: bool = False,
) -> AgentRun:
    run_task = asyncio.create_task(
        agent.start(instruction, use_model, disturbance, live_desktop)
    )
    if not json_output:
        label = "Selected model is planning..." if use_model else "Building deterministic plan..."
        await spinner(run_task, label)
    run = await run_task
    if json_output:
        if run.status == RunStatus.WAITING_APPROVAL:
            # JSON/non-interactive mode fails closed instead of silently approving.
            run = agent.approve(run.id, False)
        print(run.model_dump_json(indent=2))
        return run

    show_plan(run)
    show_trace(run)
    while run.status == RunStatus.WAITING_APPROVAL and run.pending_action:
        action = run.pending_action
        print(f"\n  {paint('APPROVAL REQUIRED', Style.BOLD, Style.YELLOW)}")
        print(f"  Action: {action.name}")
        print(f"  Expected result: {action.expected_result}")
        answer = input(f"  Approve this action once? {paint('[y/N]', Style.YELLOW)} ").strip().lower()
        run = agent.approve(run.id, answer in {"y", "yes"})
        show_trace(run)
    show_result(run)
    return run


async def interactive(
    agent: SentinelAgent,
    model: PlannerModel,
    registry: ModelRegistry,
    use_model: bool,
    live_desktop: bool,
) -> None:
    health = await model.health()
    show_health(health)
    print(paint("  Type a task, or :help for commands.", Style.GREY))
    print(
        paint(
            f"  Desktop actions are {'LIVE' if live_desktop else 'SIMULATED'}.",
            Style.YELLOW if live_desktop else Style.GREY,
        )
    )
    disturbance: str | None = None
    while True:
        try:
            instruction = input(f"\n{paint('sentinel', Style.BOLD, Style.LIME)}{paint(' > ', Style.GREY)}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not instruction:
            continue
        if instruction in {":quit", ":exit", "quit", "exit"}:
            return
        if instruction == ":help":
            print("  :model on|off    Toggle model planning")
            print("  :model use NAME  Switch model profile")
            print("  :models          List model profiles")
            print("  :popup on|off    Toggle popup disturbance")
            print("  :status          Show current configuration")
            print("  :quit            Exit SentinelAgent")
            continue
        if instruction.startswith(":model "):
            argument = instruction.removeprefix(":model ").strip()
            if argument in {"on", "off"}:
                use_model = argument == "on"
                print(f"  Model planning {'enabled' if use_model else 'disabled'}.")
            elif argument.startswith("use "):
                name = argument.removeprefix("use ").strip()
                try:
                    registry.use(name)
                    model = planner_from_profile(registry.get(name))
                    agent.planner.model = model
                    print(f"  Active model profile: {name}")
                    show_health(await model.health())
                except KeyError as exc:
                    print(f"  {paint(str(exc), Style.RED)}")
            else:
                print("  Usage: :model on|off|use NAME")
            continue
        if instruction == ":models":
            configuration = registry.load()
            for profile in configuration.profiles:
                mark = "*" if profile.name == configuration.active else " "
                print(f"  {mark} {profile.name:16} {profile.provider:20} {profile.model}")
            continue
        if instruction.startswith(":popup "):
            disturbance = "popup" if instruction.endswith("on") else None
            print(f"  Popup disturbance {'enabled' if disturbance else 'disabled'}.")
            continue
        if instruction == ":status":
            profile = registry.get()
            print(f"  model={'on' if use_model else 'off'} profile={profile.name} provider={profile.provider} id={profile.model} popup={'on' if disturbance else 'off'}")
            continue
        await execute(agent, instruction, use_model, disturbance, live_desktop)


async def async_main(args: argparse.Namespace) -> int:
    try:
        mode = getattr(args, "egress", None)
        enabled = None if mode is None else mode == "on"
        agent, model, registry = make_agent(getattr(args, "model_profile", None), enabled)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not args.json:
        banner()
    try:
        if args.task:
            run = await execute(agent, args.task, not args.no_model, args.disturbance, args.live_desktop, args.json)
            return 0 if run.status == RunStatus.COMPLETED else 1
        await interactive(agent, model, registry, not args.no_model, args.live_desktop)
        return 0
    finally:
        agent.close()


def show_models(registry: ModelRegistry) -> None:
    configuration = registry.load()
    print("ACTIVE  NAME              PROVIDER              MODEL")
    for profile in configuration.profiles:
        active = "*" if configuration.active == profile.name else ""
        print(f"{active:6}  {profile.name:16}  {profile.provider:20}  {profile.model}")


async def manage_models(args: argparse.Namespace) -> int:
    registry = ModelRegistry(get_settings().models_file)
    if args.models_command == "list":
        show_models(registry)
        return 0
    if args.models_command == "use":
        try:
            profile = registry.use(args.name)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Active model profile: {profile.name} ({profile.provider}, {profile.model})")
        return 0
    if args.models_command == "add":
        profile = ModelProfile(
            name=args.name,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            timeout=args.timeout,
        )
        registry.add(profile)
        print(f"Saved model profile: {profile.name}. No secret value was stored.")
        return 0
    if args.models_command == "test":
        try:
            profile = registry.get(args.name)
        except KeyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        health = await planner_from_profile(profile).health()
        show_health(health)
        return 0 if health.get("online") and health.get("installed") else 1
    show_models(registry)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline-first, safety-aware computer agent")
    parser.add_argument("task", nargs="?", help="Task to execute; omit for interactive mode")
    parser.add_argument("--no-model", action="store_true", help="Use the fast deterministic planner")
    parser.add_argument("--egress", choices=["on", "off"], default=None, help="Gateway mode (default on; off bypasses network policy and DLP)")
    parser.add_argument("--model-profile", help="Use this profile for the current run")
    parser.add_argument("--disturbance", choices=["popup"], help="Inject a benchmark disturbance")
    parser.add_argument("--json", action="store_true", help="Machine-readable, non-interactive output")
    parser.add_argument(
        "--live-desktop",
        action="store_true",
        help="Actually launch allowlisted Windows applications; otherwise simulate",
    )
    return parser


def build_models_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel-agent models", description="Manage model profiles")
    subparsers = parser.add_subparsers(dest="models_command")
    subparsers.add_parser("list", help="List configured model profiles")
    use = subparsers.add_parser("use", help="Persist the active profile")
    use.add_argument("name")
    add = subparsers.add_parser("add", help="Add or replace a profile")
    add.add_argument("name")
    add.add_argument("--provider", required=True, choices=["ollama", "openai_compatible", "anthropic", "gemini"])
    add.add_argument("--model", required=True)
    add.add_argument("--base-url", required=True)
    add.add_argument("--api-key-env", help="Environment variable containing the API key; never the key itself")
    add.add_argument("--timeout", type=float, default=120.0)
    test = subparsers.add_parser("test", help="Check profile configuration and connectivity")
    test.add_argument("name", nargs="?")
    return parser


def main() -> None:
    with suppress(KeyboardInterrupt):
        if len(sys.argv) > 1 and sys.argv[1] == "egress":
            parser = argparse.ArgumentParser(prog="sentinel-agent egress")
            parser.add_argument("command", choices=["status", "report", "verify"])
            parser.add_argument("run_id", nargs="?")
            args = parser.parse_args(sys.argv[2:])
            if args.command == "status":
                settings = get_settings()
                print(json.dumps({"enabled_by_default": settings.egress_enabled,
                                  "bind": "127.0.0.1", "port": settings.egress_port,
                                  "lifecycle": "one proxy per agent run; status shows configuration",
                                  "cloud_low": settings.egress_cloud_low}, indent=2))
                return
            if not args.run_id:
                parser.error("run_id is required")
            try:
                import re
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", args.run_id):
                    raise ValueError("Invalid run ID")
                path = Path("artifacts/egress") / f"{args.run_id}.jsonl"
                result = report(path) if args.command == "report" else {"valid": True, "records": len(read_verified(path))}
                print(json.dumps(result, indent=2))
            except (ValueError, OSError) as exc:
                print(f"Audit verification failed: {type(exc).__name__}", file=sys.stderr)
                raise SystemExit(1)
            return
        if len(sys.argv) > 1 and sys.argv[1] == "models":
            args = build_models_parser().parse_args(sys.argv[2:])
            raise SystemExit(asyncio.run(manage_models(args)))
        raise SystemExit(asyncio.run(async_main(build_parser().parse_args())))


if __name__ == "__main__":
    main()
