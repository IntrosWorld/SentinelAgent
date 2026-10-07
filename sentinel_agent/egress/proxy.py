"""Authenticated loopback forward proxy with pinned upstream connections."""

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.sni import InvalidClientHello, read_client_hello
from sentinel_agent.egress.telemetry import AuditLog


@dataclass(frozen=True)
class ProxyLimits:
    connect_timeout: float = 10
    idle_timeout: float = 30
    max_connections: int = 64
    max_header_bytes: int = 16384
    max_body_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        if min(self.connect_timeout, self.idle_timeout, self.max_connections,
               self.max_header_bytes, self.max_body_bytes) <= 0:
            raise ValueError("Proxy limits must be positive")


class ProxyBlocked(Exception):
    def __init__(self, rule, status=403):
        self.rule, self.status = rule, status


class EgressProxy:
    def __init__(self, policy: DestinationPolicy, audit: AuditLog, *, port=8899, limits=None):
        self.policy, self.audit, self.port = policy, audit, port
        self.limits = limits or ProxyLimits()
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.tasks = set()

    @property
    def auth_header(self):
        encoded = base64.b64encode(f"sentinel:{self.token}".encode()).decode()
        return "Basic " + encoded

    @property
    def url(self):
        return f"http://sentinel:{self.token}@127.0.0.1:{self.port}"

    async def start(self):
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", self.port,
                                                  limit=self.limits.max_header_bytes)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _accept(self, reader, writer):
        overloaded = len(self.tasks) >= self.limits.max_connections
        task = asyncio.create_task(self._handle(reader, writer, overloaded))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _headers(self, reader):
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), self.limits.idle_timeout)
        if len(raw) > self.limits.max_header_bytes:
            raise ProxyBlocked("header-limit", 431)
        lines = raw[:-4].decode("ascii").split("\r\n")
        first = lines[0].split(" ")
        if len(first) != 3 or first[2] not in {"HTTP/1.0", "HTTP/1.1"}:
            raise ProxyBlocked("malformed-request", 400)
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ProxyBlocked("malformed-header", 400)
            name = name.lower()
            if name in headers or any(ord(c) < 32 and c != "\t" for c in value):
                raise ProxyBlocked("duplicate-or-invalid-header", 400)
            headers[name] = value.strip()
        return first[0], first[1], headers

    async def _handle(self, reader, writer, overloaded=False):
        started = time.perf_counter()
        upstream = None
        established = False
        event = dict(kind="connection", method="", host="", port=0, sni=None,
                     resolved_ip=None, decision="block", rule="incomplete",
                     bytes_up=0, bytes_down=0)
        try:
            if overloaded:
                raise ProxyBlocked("connection-limit", 503)
            method, target, headers = await self._headers(reader)
            event["method"] = method if method in {"CONNECT", "GET", "POST", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"} else "OTHER"
            if not hmac.compare_digest(headers.get("proxy-authorization", ""), self.auth_header):
                raise ProxyBlocked("proxy-auth", 407)
            if method == "CONNECT":
                # CONNECT authority must have an explicit port, no userinfo/path.
                if not re.fullmatch(r"(?:\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+):[0-9]{1,5}", target):
                    raise ProxyBlocked("malformed-authority", 400)
                url = "https://" + target
            else:
                if method not in {"GET", "POST", "HEAD", "PUT", "DELETE", "PATCH", "OPTIONS"}:
                    raise ProxyBlocked("method", 405)
                url = target
            decision = await self.policy.evaluate_url(url)
            event.update(host=decision.host, port=decision.port, resolved_ip=decision.pinned_ip)
            if not decision.allowed:
                raise ProxyBlocked(decision.rule)
            if method != "CONNECT" and decision.scheme != "http":
                raise ProxyBlocked("connect-required")
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                established = True
                buffered, sni = await asyncio.wait_for(read_client_hello(reader), self.limits.idle_timeout)
                event["sni"] = sni
                if sni is not None and sni != decision.host:
                    raise ProxyBlocked("sni-mismatch")
                initial = buffered
            else:
                if "transfer-encoding" in headers or "upgrade" in headers or "expect" in headers:
                    raise ProxyBlocked("unsupported-framing", 400)
                length_text = headers.get("content-length", "0")
                if not re.fullmatch(r"[0-9]{1,10}", length_text):
                    raise ProxyBlocked("invalid-content-length", 400)
                length = int(length_text)
                if length > self.limits.max_body_bytes:
                    raise ProxyBlocked("body-limit", 413)
                body = await asyncio.wait_for(reader.readexactly(length), self.limits.idle_timeout)
                parts = urlsplit(target)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                # One HTTP request per connection: never tunnel a second request
                # or trust a mismatched Host header / hop-by-hop headers.
                remove = {"proxy-authorization", "proxy-connection", "connection", "host", "keep-alive", "te", "trailer"}
                remove.update(x.strip().lower() for x in headers.get("connection", "").split(","))
                hostname = f"[{decision.host}]" if ":" in decision.host else decision.host
                forwarded = {k: v for k, v in headers.items() if k not in remove}
                forwarded.update(host=f"{hostname}:{decision.port}", connection="close", **{"content-length": str(length)})
                initial = (f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in forwarded.items()) + "\r\n").encode("ascii") + body
            up_reader, upstream = await asyncio.wait_for(
                asyncio.open_connection(decision.pinned_ip, decision.port), self.limits.connect_timeout)
            upstream.write(initial)
            await upstream.drain()
            event.update(decision="allow", rule=decision.rule, bytes_up=len(initial))
            if method == "CONNECT":
                await self._relay(reader, writer, up_reader, upstream, event)
            else:
                while True:
                    data = await asyncio.wait_for(up_reader.read(65536), self.limits.idle_timeout)
                    if not data:
                        break
                    writer.write(data)
                    await asyncio.wait_for(writer.drain(), self.limits.idle_timeout)
                    event["bytes_down"] += len(data)
        except ProxyBlocked as exc:
            event.update(decision="block", rule=exc.rule)
            if not established:
                await self._error(writer, exc.status, exc.rule)
        except InvalidClientHello:
            event.update(decision="block", rule="invalid-client-hello")
        except (TimeoutError, asyncio.LimitOverrunError) as exc:
            rule = "timeout" if isinstance(exc, TimeoutError) else "header-limit"
            event.update(decision="block", rule=rule)
            if not established:
                await self._error(writer, 408, rule)
        except (OSError, ValueError, asyncio.IncompleteReadError):
            event.update(decision="block", rule="connection-error")
            if not established:
                await self._error(writer, 502, "connection-error")
        finally:
            try:
                for stream in (upstream, writer):
                    if stream:
                        stream.close()
                for stream in (upstream, writer):
                    if stream:
                        with suppress(OSError, TimeoutError):
                            await asyncio.wait_for(stream.wait_closed(), 1)
            finally:
                # Shutdown can cancel wait_closed after a response has arrived.
                # Recording must survive that cancellation as well.
                event["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                self.audit.write(**event)

    async def _error(self, writer, status, rule):
        phrase = {403: "Forbidden", 407: "Proxy Authentication Required"}.get(status, "Request Failed")
        challenge = 'Proxy-Authenticate: Basic realm="Sentinel"\r\n' if status == 407 else ""
        writer.write((f"HTTP/1.1 {status} {phrase}\r\nX-Sentinel-Block: {rule}\r\n"
                      + challenge + "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
        with suppress(OSError, TimeoutError):
            await asyncio.wait_for(writer.drain(), 1)

    async def _relay(self, reader, writer, up_reader, upstream, event):
        last_activity = time.monotonic()

        async def pump(source, destination, counter):
            nonlocal last_activity
            while True:
                try:
                    data = await asyncio.wait_for(source.read(65536), self.limits.idle_timeout)
                except TimeoutError:
                    if time.monotonic() - last_activity >= self.limits.idle_timeout:
                        raise
                    continue
                if not data:
                    with suppress(OSError, NotImplementedError):
                        destination.write_eof()
                    return
                destination.write(data)
                await asyncio.wait_for(destination.drain(), self.limits.idle_timeout)
                last_activity = time.monotonic()
                event[counter] += len(data)

        tasks = [asyncio.create_task(pump(reader, upstream, "bytes_up")),
                 asyncio.create_task(pump(up_reader, writer, "bytes_down"))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
