"""Deterministic adversarial scenarios. No external traffic or real secrets."""

import argparse
import ast
import asyncio
import json
from pathlib import Path
import tempfile

from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.dlp import DLPScanner
from sentinel_agent.egress.lab import client_hello, tls_lab
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.telemetry import read_verified
from sentinel_agent.models import DEFAULT_PROFILES


def bypasses(source):
    return any(
        isinstance(node, ast.Import) and any(n.name.split(".")[0] == "httpx" for n in node.names)
        or isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "httpx"
        for node in ast.walk(ast.parse(source))
    )


async def evaluate():
    rows = []
    scanner = DLPScanner()
    config = EgressConfig.from_profiles(DEFAULT_PROFILES)

    async def public(host, port):
        return ["8.8.8.8"]

    policy = DestinationPolicy(config, public)

    def record(identifier, expected, actual, passed):
        rows.append({"id": identifier, "expected": expected, "actual": actual, "passed": bool(passed)})

    for identifier, text in (("A1", "AKIA" + "A" * 16), ("A2", "4111111111111111")):
        result = scanner.protect({"prompt": text}, "model-provider")
        record(identifier, "block", result.action, not result.allowed)
    # A3 intentionally uses the strict low-severity option. The default table
    # permits cloud email; this resolves the spec's contradictory A3 expectation.
    result = DLPScanner(cloud_low="redact").protect("person@example.com", "model-provider")
    record("A3", "redact", result.action, result.action == "redact")
    for identifier, url, rule in (
        ("A4", "https://attacker.example", "not-allowlisted"),
        ("A6", "http://169.254.169.254", "ip-literal"),
        ("A8", "https://8.8.8.8", "ip-literal"),
        ("A9", "http://api.openai.com", "tls-required"),
        ("A11", "https://xn--openai-lookalike.example", "suspicious-host"),
        ("A12", "https://api.openai.com:8443", "port"),
    ):
        result = await policy.evaluate_url(url)
        record(identifier, rule, result.rule, not result.allowed and result.rule == rule)
    blocked = []
    for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1"):
        async def private(host, port, address=ip):
            return [address]
        result = await DestinationPolicy(config, private).evaluate_url("https://api.openai.com")
        blocked.append(not result.allowed and result.rule == "private-ip")
    record("A5", "private-ip", "private-ip" if all(blocked) else "fail", all(blocked))
    calls = 0

    async def rebound(host, port):
        nonlocal calls
        calls += 1
        return ["8.8.8.8"] if calls == 1 else ["127.0.0.1"]

    rebinding = DestinationPolicy(config, rebound)
    first = await rebinding.evaluate_url("https://api.openai.com")
    second = await rebinding.evaluate_url("https://api.openai.com")
    good = first.pinned_ip == "8.8.8.8" and not second.allowed
    record("A7", "pin-public-or-block", "pin-public-or-block" if good else "fail", good)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        async with tls_lab(root, "redteam") as (proxy, url, context):
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            authority = url.removeprefix("https://").rstrip("/")
            writer.write(f"CONNECT {authority} HTTP/1.1\r\nProxy-Authorization: {proxy.auth_header}\r\n\r\n".encode())
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")
            writer.write(client_hello("attacker.example"))
            await writer.drain()
            closed = await asyncio.wait_for(reader.read(), 3) == b""
            writer.close()
            await writer.wait_closed()
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            writer.write(f"CONNECT {authority} HTTP/1.1\r\n\r\n".encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 3)
            writer.close()
            await writer.wait_closed()
        records = read_verified(root / "redteam.jsonl")
        mismatch = closed and any(r["rule"] == "sni-mismatch" for r in records)
        record("A10", "sni-mismatch", "sni-mismatch" if mismatch else "fail", mismatch)
        record("A13", "407", "407" if b"407" in response else "fail", b"407" in response)
    package = Path(__file__).parents[1]
    offenders = [p for p in package.rglob("*.py") if "egress" not in p.relative_to(package).parts
                 and bypasses(p.read_text(encoding="utf-8"))]
    guard = not offenders and bypasses("from httpx import AsyncClient as bypass")
    record("A14", "static-guard", "static-guard" if guard else "fail", guard)

    local = scanner.protect("person@example.com", "local-model")
    target = await policy.evaluate_url("http://127.0.0.1:11434")
    record("L1", "allow", local.action, local.allowed and target.allowed)
    result = scanner.protect("Explain Python functions", "model-provider")
    target = await policy.evaluate_url("https://api.openai.com")
    record("L2", "allow", result.action, result.allowed and target.allowed)
    target = await policy.evaluate_url("https://www.youtube.com/results?search_query=hello")
    record("L3", "allow", "allow" if target.allowed else target.rule, target.allowed)
    result = scanner.protect("4111111111111112 and 234567890125", "model-provider")
    record("L4", "allow", result.action, result.allowed and not result.findings)
    rows.sort(key=lambda r: (r["id"][0], int(r["id"][1:])))
    attacks = [r for r in rows if r["id"].startswith("A")]
    legitimate = [r for r in rows if r["id"].startswith("L")]
    block_cases = [r for r in attacks if r["id"] not in {"A3", "A7", "A14"}]
    return {"attacks_passed": sum(r["passed"] for r in attacks), "attacks_total": len(attacks),
            "block_cases_passed": sum(r["passed"] for r in block_cases), "block_cases_total": len(block_cases),
            "block_rate_percent": 100 * sum(r["passed"] for r in block_cases) / len(block_cases),
            "false_positive_rate_percent": 100 * sum(not r["passed"] for r in legitimate) / len(legitimate),
            "legitimate_total": len(legitimate), "scenarios": rows,
            "scope": "Offline unit policy/DLP checks plus real loopback SNI/auth checks; A3 uses cloud_low=redact"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(evaluate())
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    raise SystemExit(0 if all(r["passed"] for r in result["scenarios"]) else 1)


if __name__ == "__main__":
    main()
