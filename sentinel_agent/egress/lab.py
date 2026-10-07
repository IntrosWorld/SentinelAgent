"""Offline TLS lab used only by validation/measurement commands (dev extras)."""

import asyncio
from contextlib import asynccontextmanager, suppress
import ssl

from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import EgressProxy
from sentinel_agent.egress.telemetry import AuditLog


def client_hello(host):
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    stream = context.wrap_bio(incoming, outgoing, server_hostname=host)
    with suppress(ssl.SSLWantReadError):
        stream.do_handshake()
    return outgoing.read()


@asynccontextmanager
async def tls_lab(root, run_id="lab", payload_size=2):
    import trustme  # Test/benchmark dependency, never needed by the gateway.

    ca = trustme.CA()
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ca.issue_cert("localhost", "127.0.0.1").configure_cert(server_context)
    client_context = ssl.create_default_context()
    ca.configure_trust(client_context)
    tasks = set()

    async def handler(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            size = 0
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-length:"):
                    size = int(line.split(b":")[1])
            if size:
                await reader.readexactly(size)
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {payload_size}\r\nConnection: close\r\n\r\n".encode())
            remaining = payload_size
            while remaining:
                count = min(65536, remaining)
                writer.write(b"x" * count)
                await writer.drain()
                remaining -= count
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]

    async def resolver(host, port):
        return ["127.0.0.1"]

    config = EgressConfig(local_endpoints=frozenset({("localhost", port, "https"), ("127.0.0.1", port, "https")}))
    audit = AuditLog(run_id, root)
    proxy = await EgressProxy(DestinationPolicy(config, resolver), audit, port=0).start()
    try:
        yield proxy, f"https://localhost:{port}/", client_context
    finally:
        await proxy.close()
        server.close()
        await server.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*list(tasks), return_exceptions=True)
