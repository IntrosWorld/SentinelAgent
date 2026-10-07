import asyncio
from contextlib import asynccontextmanager
import json
import ssl

import httpx
import pytest
import trustme

from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import EgressProxy, ProxyLimits
from sentinel_agent.egress.sni import InvalidClientHello, client_hello_sni, read_client_hello
from sentinel_agent.egress.telemetry import AuditLog, read_verified, report


def hello(host="localhost"):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    stream = context.wrap_bio(incoming, outgoing, server_hostname=host)
    try:
        stream.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


@asynccontextmanager
async def setup_proxy(tmp_path, *, tls=False, limits=None):
    requests = []
    ca = trustme.CA()
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("localhost").configure_cert(server_context)
    client_context = ssl.create_default_context()
    ca.configure_trust(client_context)

    async def handler(reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            requests.append(head)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_context if tls else None)
    port = server.sockets[0].getsockname()[1]
    scheme = "https" if tls else "http"
    config = EgressConfig(local_endpoints=frozenset({("localhost", port, scheme)}))

    async def resolver(host, port):
        return ["127.0.0.1"]

    audit = AuditLog("proxy-test", tmp_path)
    proxy = await EgressProxy(DestinationPolicy(config, resolver), audit, port=0, limits=limits).start()
    try:
        yield proxy, f"{scheme}://localhost:{port}", client_context, requests
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()


async def raw(proxy, request):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(request)
    await writer.drain()
    response = await asyncio.wait_for(reader.read(), 3)
    writer.close()
    await writer.wait_closed()
    return response


async def test_real_tls_hello_and_fragmentation():
    wire = hello("api.openai.com")
    assert client_hello_sni(wire[5:]) == "api.openai.com"
    data = wire[5:]
    split = 37
    chunks = [data[:split], data[split:]]
    fragmented = b"".join(b"\x16\x03\x01" + len(c).to_bytes(2, "big") + c for c in chunks)
    reader = asyncio.StreamReader()
    reader.feed_data(fragmented)
    reader.feed_eof()
    assert await read_client_hello(reader) == (fragmented, "api.openai.com")
    assert client_hello_sni(hello("127.0.0.1")[5:]) is None
    for cut in (0, 1, 10, len(data) - 1):
        with pytest.raises(InvalidClientHello):
            client_hello_sni(data[:cut])


@pytest.mark.parametrize("tls", [False, True])
async def test_actual_proxy_relay(tmp_path, tls):
    async with setup_proxy(tmp_path, tls=tls) as (proxy, url, context, requests):
        async with httpx.AsyncClient(proxy=proxy.url, trust_env=False, verify=context) as client:
            response = await client.get(url + "/hello")
        assert response.status_code == 200 and response.text == "ok"
        assert b"proxy-authorization" not in requests[0].lower()
    records = read_verified(tmp_path / "proxy-test.jsonl")
    assert len(records) == 1 and records[0]["decision"] == "allow"
    assert records[0]["resolved_ip"] == "127.0.0.1"
    assert records[0]["bytes_up"] > 0 and records[0]["bytes_down"] > 0


async def test_auth_deny_and_server_survives_bad_request(tmp_path):
    async with setup_proxy(tmp_path) as (proxy, url, context, requests):
        response = await raw(proxy, b"GET http://bad.example/ HTTP/1.1\r\n\r\n")
        assert b"407" in response and b"proxy-auth" in response
        malformed = await raw(proxy, b"garbage\r\n\r\n")
        assert b"400" in malformed
        blocked = await raw(proxy, ("CONNECT 169.254.169.254:443 HTTP/1.1\r\nProxy-Authorization: " + proxy.auth_header + "\r\n\r\n").encode())
        assert b"403" in blocked and b"ip-literal" in blocked
        async with httpx.AsyncClient(proxy=proxy.url, trust_env=False) as client:
            assert (await client.get(url)).text == "ok"


async def test_sni_mismatch_does_not_connect_upstream(tmp_path):
    async with setup_proxy(tmp_path, tls=True) as (proxy, url, context, requests):
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        authority = url.removeprefix("https://")
        writer.write(f"CONNECT {authority} HTTP/1.1\r\nProxy-Authorization: {proxy.auth_header}\r\n\r\n".encode())
        await writer.drain()
        assert b"200" in await reader.readuntil(b"\r\n\r\n")
        writer.write(hello("attacker.example"))
        await writer.drain()
        assert await asyncio.wait_for(reader.read(), 2) == b""
        writer.close()
        await writer.wait_closed()
        assert not requests
    assert read_verified(tmp_path / "proxy-test.jsonl")[-1]["rule"] == "sni-mismatch"


async def test_http_host_rewritten_and_pipelining_not_forwarded(tmp_path):
    async with setup_proxy(tmp_path) as (proxy, url, context, requests):
        response = await raw(proxy, (f"GET {url}/ HTTP/1.1\r\nHost: attacker.example\r\nProxy-Authorization: {proxy.auth_header}\r\n\r\nGET http://attacker.example/ HTTP/1.1\r\n\r\n").encode())
        assert b"200" in response and len(requests) == 1
        assert b"attacker.example" not in requests[0]


async def test_header_idle_limit(tmp_path):
    async with setup_proxy(tmp_path, limits=ProxyLimits(idle_timeout=.05)) as (proxy, *_):
        response = await raw(proxy, b"GET ")
        assert b"408" in response
    assert read_verified(tmp_path / "proxy-test.jsonl")[-1]["rule"] == "timeout"


def test_audit_tampering_and_statistics(tmp_path):
    audit = AuditLog("test", tmp_path)
    audit.write(kind="connection", host="test.example", decision="allow", rule="approved-site", bytes_up=10, duration_ms=5)
    audit.write(kind="connection", host="bad.example", decision="block", rule="not-allowlisted", duration_ms=15)
    stats = report(audit.path)
    assert stats["connections"] == 2 and stats["bytes_up"] == 10
    assert stats["p50_ms"] == 10
    text = audit.path.read_text().replace('"bytes_up":10', '"bytes_up":99')
    audit.path.write_text(text)
    with pytest.raises(ValueError, match="record 1"):
        read_verified(audit.path)
