"""Agent HTTP boundary and per-run gateway lifetime.

The proxy loop runs in a thread so synchronous approval prompts and desktop
actions cannot suspend traffic forwarding. All audit writes use that loop.
"""

import asyncio
import atexit
from contextvars import ContextVar
from contextlib import contextmanager, suppress
from pathlib import Path
import threading
from uuid import uuid4

import httpx

from sentinel_agent.config import get_settings
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.dlp import DLPScanner
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import EgressProxy
from sentinel_agent.egress.telemetry import AuditLog
from sentinel_agent.models import ModelRegistry


class EgressError(RuntimeError):
    """A value-free HTTP/policy failure safe to include in the planner trace."""


class EgressBlocked(EgressError):
    def __init__(self, rule):
        self.rule = rule
        super().__init__(f"Outbound request blocked: {rule}")


current_gateway: ContextVar = ContextVar("sentinel_egress", default=None)


class EgressGateway:
    def __init__(self, run_id=None, *, enabled=None, policy=None, audit_root=None, port=None, scanner=None):
        settings = get_settings()
        config = EgressConfig.from_profiles(ModelRegistry(settings.models_file).list(),
                                           approved_sites=tuple(settings.egress_approved_sites))
        self.policy = policy or DestinationPolicy(config)
        self.enabled = settings.egress_enabled if enabled is None else enabled
        self.port = settings.egress_port if port is None else port
        self.scanner = scanner or DLPScanner(redact_high=settings.egress_redact_high,
                                            cloud_low=settings.egress_cloud_low,
                                            low_enabled=settings.egress_low_enabled)
        self.audit = AuditLog(run_id or uuid4().hex[:10], audit_root or Path("artifacts/egress"))
        self.proxy = None
        self.loop = None
        self.thread = None
        self.events = []

    def start(self):
        if self.loop is not None:
            return self
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        async def initialize():
            if self.enabled:
                self.proxy = await EgressProxy(self.policy, self.audit, port=self.port).start()

        try:
            self.call(initialize())
        except Exception:
            self.close()
            raise
        atexit.register(self.close)
        return self

    def submit(self, coroutine):
        if self.loop is None or not self.loop.is_running():
            coroutine.close()
            raise EgressError("Gateway is not running")
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def call(self, coroutine):
        return self.submit(coroutine).result(timeout=15)

    async def check(self, url):
        return await asyncio.wrap_future(self.submit(self.policy.evaluate_url(url)))

    def check_sync(self, url):
        return self.call(self.policy.evaluate_url(url))

    def approve_sync(self, decision):
        return self.call(self.policy.approve(decision.host, decision.port, decision.scheme))

    def record(self, **fields):
        async def write():
            self.audit.write(**fields)
            self.events.append(fields)
        self.call(write())

    @contextmanager
    def activate(self):
        token = current_gateway.set(self)
        try:
            yield self
        finally:
            current_gateway.reset(token)

    def close(self):
        if self.loop is None:
            return
        if self.proxy:
            with suppress(Exception):
                self.call(self.proxy.close())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
        self.loop = None
        atexit.unregister(self.close)


class EgressClient:
    """HTTP JSON calls only. Redirects and environment proxy settings are disabled."""

    def __init__(self, *, timeout=120, gateway=None):
        self.timeout = timeout
        self.gateway = gateway or current_gateway.get()
        self.owned = self.gateway is None
        self.client = None

    async def __aenter__(self):
        if self.owned:
            self.gateway = EgressGateway().start()
        self.client = httpx.AsyncClient(
            timeout=self.timeout, trust_env=False, follow_redirects=False,
            proxy=self.gateway.proxy.url if self.gateway.enabled else None,
        )
        return self

    async def __aexit__(self, *args):
        try:
            await self.client.aclose()
        finally:
            if self.owned:
                self.gateway.close()

    async def get(self, url, *, headers=None):
        return await self.request("GET", url, headers=headers)

    async def post(self, url, *, headers=None, json=None):
        return await self.request("POST", url, headers=headers, json=json)

    async def request(self, method, url, *, headers=None, json=None):
        gateway = self.gateway
        if gateway.enabled:
            decision = await gateway.check(url)
            if not decision.allowed:
                gateway.record(kind="request", method=method, host=decision.host, port=decision.port,
                               decision="block", rule=decision.rule)
                raise EgressBlocked(decision.rule)
            result = gateway.scanner.protect(json, decision.category)
            gateway.record(kind="request", method=method, host=decision.host, port=decision.port,
                           decision="allow" if result.allowed else "block", rule="dlp", dlp=result.summary())
            if not result.allowed:
                raise EgressBlocked("dlp")
            json = result.body
        try:
            kwargs = {"headers": headers}
            if method != "GET":
                kwargs["json"] = json
            response = await self.client.request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                raise EgressBlocked("redirect")
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            # Never stringify httpx errors: they can contain proxy credentials.
            raise EgressError(f"Outbound HTTP failed ({type(exc).__name__})") from None
