"""Private JSON-lines desktop bridge. No listening HTTP server or shell API."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from sentinel_agent.computer_use.desktop_agent import AdaptiveDesktopAgent
from sentinel_agent.computer_use.desktop_planner import DesktopPlanner
from sentinel_agent.computer_use.desktop_schema import DesktopTask
from sentinel_agent.computer_use.desktop_windows import WindowsDesktopSession
from sentinel_agent.egress.client import EgressClient, EgressGateway
from sentinel_agent.egress.config import EgressConfig, is_local_host, parse_destination
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.models import ModelProfile, ModelRegistry


MAX_LINE = 64 * 1024


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=80)
    method: str = Field(min_length=1, max_length=80)
    params: dict = Field(default_factory=dict)


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    goal: str = Field(min_length=3, max_length=2000)
    mode: Literal["desktop", "documents", "auto"] = "desktop"
    profile: str = "local-vision"
    window_id: int = Field(default=0, ge=0)
    vision: bool = True
    ocr: bool = False
    single_step: bool = True
    allow_cloud: bool = False
    max_seconds: float = Field(default=300.0, gt=0, le=900)
    expect_text: str | None = Field(default=None, min_length=1, max_length=300)


def credential_name(name: str) -> str:
    return "SENTINEL_DESKTOP_KEY_" + hashlib.sha256(name.encode()).hexdigest()[:24].upper()


def validated_profile(raw: dict) -> ModelProfile:
    allowed = set(ModelProfile.model_fields)
    if set(raw) - allowed:
        raise ValueError("Unknown profile field")
    profile = ModelProfile.model_validate(raw)
    if len(profile.name) > 80 or len(profile.model) > 200 or not 1 <= profile.timeout <= 900:
        raise ValueError("Invalid profile limits")
    if len(profile.base_url) > 2000:
        raise ValueError("Invalid endpoint")
    host, port, scheme = parse_destination(profile.base_url)
    parts = urlsplit(profile.base_url)
    if parts.query or parts.fragment or (not is_local_host(host) and scheme != "https"):
        raise ValueError("Use a local endpoint or remote HTTPS endpoint without query credentials")
    if host == "localhost":
        host = "127.0.0.1"
    authority = f"[{host}]" if ":" in host else host
    return profile.model_copy(update={
        "base_url": urlunsplit((scheme, f"{authority}:{port}", parts.path.rstrip("/"), "", "")),
        "api_key_env": credential_name(profile.name),
    })


def atomic_json(path: Path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2), encoding="utf-8")
    temporary.replace(path)


class DesktopService:
    def __init__(self, root: Path, emit):
        self.root, self.emit = root, emit
        root.mkdir(parents=True, exist_ok=True)
        self.registry = ModelRegistry(root / "models.json")
        self.history_path = root / "history.json"
        try:
            if self.history_path.stat().st_size > 1024 * 1024:
                raise ValueError()
            self.history = json.loads(self.history_path.read_text(encoding="utf-8"))
            if not isinstance(self.history, list):
                raise ValueError()
            self.history = [item for item in self.history[-50:] if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            self.history = []
        # A previous process cannot still own a runnable/approvable session.
        for item in self.history:
            if item.get("status") in {"running", "approval"}:
                item.update(status="interrupted", reason="Application closed before completion")
        self.running = None
        self.run_id = None
        self.pending = None

    def idle(self):
        if self.running is not None and not self.running.done():
            raise ValueError("Stop the current task before changing configuration")

    def event(self, kind, **data):
        self.emit({"event": kind, "run_id": self.run_id, **data})

    def persist(self):
        self.history = self.history[-50:]
        atomic_json(self.history_path, self.history)

    async def approval(self, details):
        token = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending = (token, future)
        self.event("approval", token=token, details=details)
        try:
            return await future
        finally:
            self.pending = None
            self.event("approval_closed", token=token)

    async def dispatch(self, method: str, params: dict):
        if method == "status":
            return {"version": "0.3.2", "workspace": str(self.root), "platform": sys.platform,
                    "ocr_available": shutil.which("tesseract") is not None,
                    "active_run": self.run_id if self.running and not self.running.done() else None}
        if method == "models.list":
            return self.registry.safe_view()
        if method == "models.save":
            self.idle()
            profile = validated_profile(params)
            config = self.registry.load()
            config.profiles = [p for p in config.profiles if p.name != profile.name] + [profile]
            config.active = profile.name
            atomic_json(self.registry.path, config.model_dump())
            return profile.model_dump()
        if method == "credentials.set":
            self.idle()
            name, key = params.get("name"), params.get("key")
            if not isinstance(name, str) or not isinstance(key, str) or len(key) > 4096:
                raise ValueError("Invalid credential")
            os.environ[credential_name(name)] = key
            return {"configured": bool(key)}
        if method == "models.discover":
            profile = validated_profile({"name": "discovery", "provider": params.get("provider", "ollama"),
                                         "model": "discovery", "base_url": params.get("base_url", "")})
            host, _, _ = parse_destination(profile.base_url)
            if not is_local_host(host) or profile.provider not in {"ollama", "openai_compatible"}:
                raise ValueError("Discovery is limited to local Ollama or compatible servers")
            gateway = EgressGateway(enabled=True, policy=DestinationPolicy(EgressConfig.from_profiles([profile], approved_sites=()))).start()
            try:
                with gateway.activate():
                    async with EgressClient(timeout=8) as client:
                        suffix = "/api/tags" if profile.provider == "ollama" else "/models"
                        data = (await client.get(profile.base_url + suffix)).json()
                entries = data.get("models", []) if profile.provider == "ollama" else data.get("data", [])
                return {"online": True, "models": [{"name": str(m.get("name") or m.get("id"))[:200],
                                                       "size": m.get("size", 0)} for m in entries[:200]]}
            finally:
                gateway.close()
        if method == "models.test":
            from sentinel_agent.llm import planner_from_profile
            profile = self.registry.get(params.get("name"))
            if profile.provider in {"anthropic", "gemini"}:
                return {"configured": bool(profile.api_key), "online": None, "installed": None,
                        "note": "Credentials configured only; no billable inference or authentication check performed."}
            gateway = EgressGateway(enabled=True, policy=DestinationPolicy(EgressConfig.from_profiles([profile], approved_sites=()))).start()
            try:
                with gateway.activate():
                    result = await planner_from_profile(profile).health()
                result.pop("error", None)
                return result
            finally:
                gateway.close()
        if method == "windows.list":
            self.idle()
            return WindowsDesktopSession.list_windows()
        if method == "history.list":
            return list(reversed(self.history))
        if method == "run.start":
            self.idle()
            request = StartRun.model_validate(params)
            from sentinel_agent.desktop_documents import DesktopDocuments, is_document_goal
            if request.mode == "auto":
                request = request.model_copy(update={"mode": "documents" if is_document_goal(request.goal) else "desktop"})
            if request.mode == "documents":
                documents = DesktopDocuments(self.root)
                documents.prepare(documents.plan(request.goal))
            if request.mode == "desktop" and request.window_id <= 0:
                raise ValueError("Choose a target window")
            # Validate before claiming a task has started or opening a session.
            if request.mode == "desktop":
                DesktopPlanner(self.registry.get(request.profile), vision=request.vision, ocr=request.ocr,
                               allow_cloud_observations=request.allow_cloud)
            self.run_id = uuid4().hex
            self.running = asyncio.create_task(self.execute(request, self.run_id))
            return {"run_id": self.run_id}
        if method == "run.approve":
            if (params.get("run_id") != self.run_id or self.pending is None
                    or params.get("token") != self.pending[0] or type(params.get("approved")) is not bool
                    or self.pending[1].done()):
                raise ValueError("Approval expired or does not match this action")
            self.pending[1].set_result(params["approved"])
            return {"accepted": True}
        if method == "run.cancel":
            if params.get("run_id") != self.run_id:
                raise ValueError("Task does not match")
            if self.running and not self.running.done():
                self.running.cancel()
            return {"requested": True}
        raise ValueError("Unknown desktop method")

    async def execute(self, request, run_id):
        entry = {"id": run_id, "goal": request.goal, "mode": request.mode, "profile": request.profile,
                 "created": datetime.now(timezone.utc).isoformat(), "status": "running"}
        self.history.append(entry)
        gateway = None
        try:
            with suppress(OSError):
                self.persist()
            self.event("started", task=entry.copy())
            if request.mode == "documents":
                from sentinel_agent.desktop_documents import DesktopDocuments
                documents = DesktopDocuments(self.root)
                action = documents.plan(request.goal)
                target = documents.path(action.arguments["filename"])
                fingerprint = documents.fingerprint(target)
                if action.name.startswith(("append_", "edit_")) and fingerprint is None:
                    raise ValueError("Import or create the document first")
                approved = await self.approval({
                    "window": "Local document workspace", "mode": "documents",
                    "action": {"kind": action.name, **action.arguments},
                    "output": str(target), "replaces_existing": fingerprint is not None,
                })
                if not approved:
                    outcome = {"status": "cancelled", "reason": "Document action declined", "steps": 0, "trace": []}
                else:
                    self.event("progress", message="Writing and reopening the document for verification")
                    operation = asyncio.create_task(asyncio.to_thread(documents.execute, action, fingerprint))
                    try:
                        artifact = await asyncio.shield(operation)
                    except asyncio.CancelledError:
                        # A running file write is allowed to finish atomically before Stop completes.
                        artifact = await operation
                        outcome = {"status": "cancelled", "reason": "Stopped after the approved file write finished",
                                   "steps": 1, "trace": [], "artifacts": [artifact]}
                    else:
                        outcome = {"status": "completed", "reason": "Document written and verified",
                                   "steps": 1, "trace": [], "artifacts": [artifact]}
            else:
                profile = self.registry.get(request.profile)
                planner = DesktopPlanner(profile, vision=request.vision, ocr=request.ocr,
                                         allow_cloud_observations=request.allow_cloud)
                canonical = profile.model_copy(update={"base_url": planner.base_url})
                gateway = EgressGateway(enabled=True, policy=DestinationPolicy(EgressConfig.from_profiles([canonical], approved_sites=())),
                                        proxy_limits=ProxyLimits(idle_timeout=min(profile.timeout, request.max_seconds))).start()
                session = WindowsDesktopSession(request.window_id, capture_images=request.vision)
                service = self
                class ReportingPlanner:
                    async def next_action(self, goal, snapshot, history):
                        service.event("progress", message="Observing and planning", elements=len(snapshot.elements))
                        return await planner.next_action(goal, snapshot, history)
                async def approve(action, snapshot):
                    if action.text is not None:
                        scan = gateway.scanner.protect(action.text, "approved-site")
                        if not scan.allowed or scan.body != action.text:
                            return False
                    target = next((e for e in snapshot.elements if e.id == action.element_id), None)
                    approved = await self.approval({"window": snapshot.title, "action": action.model_dump(exclude_none=True),
                                                    "target": target.model_dump() if target else None})
                    if approved:
                        await session.focus()
                    return approved
                task = DesktopTask(goal=request.goal, expect_text=request.expect_text,
                                   single_step=request.single_step, max_seconds=request.max_seconds)
                with gateway.activate():
                    outcome = (await AdaptiveDesktopAgent(ReportingPlanner(), session, approve).run(task)).model_dump()
            entry.update(status=outcome["status"], reason=outcome["reason"], steps=outcome["steps"])
            if outcome.get("artifacts"):
                entry["artifacts"] = outcome["artifacts"]
            self.event("finished", result=outcome)
        except asyncio.CancelledError:
            entry.update(status="cancelled", reason="Stopped by user")
            self.event("finished", result={"status": "cancelled", "reason": "Stopped by user", "steps": 0, "trace": []})
        except Exception as exc:
            entry.update(status="failed", reason=type(exc).__name__)
            self.event("finished", result={"status": "failed", "reason": type(exc).__name__, "steps": 0, "trace": []})
        finally:
            if gateway:
                gateway.close()
            self.pending = None
            with suppress(OSError):
                self.persist()

    async def close(self):
        if self.running and not self.running.done():
            self.running.cancel()
            with suppress(asyncio.CancelledError):
                await self.running


async def serve(root: Path, source, sink):
    def emit(value):
        sink.write(json.dumps(value, ensure_ascii=True) + "\n")
        sink.flush()
    service = DesktopService(root, emit)
    jobs = set()
    async def handle(request):
        try:
            result = await service.dispatch(request.method, request.params)
            emit({"id": request.id, "result": result})
        except Exception as exc:
            # Never expose provider errors, secrets or screen contents in errors.
            emit({"id": request.id, "error": {"type": type(exc).__name__, "message": "Request failed. Check configuration, connection and task state."}})
    try:
        while True:
            line = await asyncio.to_thread(source.readline, MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE or not line.endswith("\n"):
                break  # Refuse an oversized/truncated protocol stream.
            try:
                request = Request.model_validate_json(line)
            except ValueError:
                emit({"error": {"type": "InvalidRequest", "message": "Invalid desktop request"}})
                continue
            if len(jobs) >= 32:
                emit({"id": request.id, "error": {"type": "Busy", "message": "Too many requests"}})
                continue
            job = asyncio.create_task(handle(request))
            jobs.add(job)
            job.add_done_callback(jobs.discard)
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        await service.close()


def main():
    import argparse
    import importlib
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.data_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    os.environ["SENTINEL_MODELS_FILE"] = str(root / "models.json")
    # Initialize optional C-extension dependencies before the stdin reader starts.
    # On Windows, numerical-library startup can acquire the CRT stream lock held
    # by a concurrent blocking readline, stalling the private protocol.
    for module in ("openpyxl", "docx", "pypdf", "reportlab.platypus"):
        with suppress(ImportError):
            importlib.import_module(module)
    # Third-party prints must never corrupt the private JSON protocol.
    sink, sys.stdout = sys.stdout, sys.stderr
    asyncio.run(serve(root, sys.stdin, sink))


if __name__ == "__main__":
    main()
