"""End-to-end adaptive runs: actual Chrome, fixture UI, authenticated proxy."""

import asyncio
import os
from urllib.parse import urlsplit

import pytest

from test_computer_browser import browser_executable, browser_url
from sentinel_agent.computer_use.agent import AdaptiveBrowserAgent
from sentinel_agent.computer_use.browser import BrowserSession
from sentinel_agent.computer_use.planner import BrowserPlanner
from sentinel_agent.computer_use.schema import BrowserAction, BrowserTask
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.llm import OllamaPlanner


@pytest.fixture
def gateway(tmp_path, browser_url):
    async def resolver(host, port):
        if host != "127.0.0.1":
            raise OSError("Offline test DNS")
        return ["127.0.0.1"]
    endpoints = frozenset({("127.0.0.1", urlsplit(browser_url).port, "http"), ("127.0.0.1", 11434, "http")})
    policy = DestinationPolicy(EgressConfig(local_endpoints=endpoints, approved_sites=frozenset()), resolver)
    result = EgressGateway(policy=policy, audit_root=tmp_path,
                           proxy_limits=ProxyLimits(idle_timeout=180)).start()
    try:
        yield result
    finally:
        result.close()


class ObservingPlanner:
    """Deterministic test decision maker, never a production model fallback."""
    async def next_action(self, goal, snapshot, history):
        field = next(e for e in snapshot.elements if e.name == "Search query")
        if field.value != "hello world":
            return BrowserAction(kind="fill", snapshot_id=snapshot.id, element_id=field.id, text="hello world")
        button = next(e for e in snapshot.elements if e.name in {"Search", "Find"})
        return BrowserAction(kind="click", snapshot_id=snapshot.id, element_id=button.id)


@pytest.mark.parametrize("disturbed", [False, True])
async def test_observe_act_verify_real_browser(gateway, browser_url, browser_executable, disturbed):
    session = BrowserSession(gateway, browser_executable)
    approvals = []

    async def approval(action, snapshot):
        approvals.append(action.kind)
        if disturbed and len(approvals) == 1:
            async def mutate():
                await session._page.evaluate("document.getElementById('search').textContent='Find'")
            await asyncio.wrap_future(gateway.submit(mutate()))
        # Approval is test-only and scoped to the local fixture's two controls.
        return action.kind in {"fill", "click"}

    task = BrowserTask(goal="Search for hello world", url=browser_url, expect_text="Results for hello world", expect_url=browser_url)
    result = await AdaptiveBrowserAgent(ObservingPlanner(), session, approval).run(task)
    assert result.status == "completed", result.model_dump()
    assert result.steps == 2
    assert approvals == (["fill", "fill", "click"] if disturbed else ["fill", "click"])
    observations = [e["snapshot_id"] for e in result.trace if e["kind"] == "observe"]
    assert len(set(observations)) == len(observations)
    assert bool([e for e in result.trace if e["kind"] == "recovery"]) == disturbed
    assert session._page is None


@pytest.mark.skipif(os.environ.get("SENTINEL_LIVE_MODEL_TEST") != "1", reason="Opt-in installed local Ollama evaluation")
async def test_local_7b_plans_real_browser(gateway, browser_url, browser_executable):
    diagnostics = []
    class InspectedLocalModel(OllamaPlanner):
        async def create_plan(self, *args):
            try:
                plan = await super().create_plan(*args)
                diagnostics.append({"actions": [a.name for a in plan.actions],
                                    "argument_keys": [sorted(a.arguments) for a in plan.actions]})
                return plan
            except Exception as exc:
                diagnostics.append({"error_type": type(exc).__name__,
                                    "stage": "invalid-plan" if str(exc).startswith("The local model returned") else "transport",
                                    "cause_type": type(exc.__cause__).__name__})
                raise
    planner = BrowserPlanner(InspectedLocalModel("http://127.0.0.1:11434", "qwen2.5-coder:7b", timeout=180))
    session = BrowserSession(gateway, browser_executable)

    async def approve_fixture_only(action, snapshot):
        target = next(e for e in snapshot.elements if e.id == action.element_id)
        return snapshot.url == browser_url and target.name in {"Search query", "Search"}

    task = BrowserTask(goal="Enter hello world into Search query, then click Search. Do not modify other controls.",
                       url=browser_url, expect_text="Results for hello world", expect_url=browser_url,
                       max_seconds=360, max_steps=5)
    with gateway.activate():
        result = await AdaptiveBrowserAgent(planner, session, approve_fixture_only).run(task)
    assert result.status == "completed", {"result": result.model_dump(), "model": diagnostics}
    assert result.steps >= 2
