"""Default-deny destination checks. Allowed decisions carry a validated IP.

This module does not open upstream connections. The proxy must connect to
decision.pinned_ip, never resolve decision.host again after evaluation.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from ipaddress import ip_address
import socket

from sentinel_agent.egress.config import EgressConfig, normalize_host, parse_destination

Resolver = Callable[[str, int], Awaitable[Sequence[str]]]


async def resolve_host(host: str, port: int) -> Sequence[str]:
    records = await asyncio.get_running_loop().getaddrinfo(
        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(dict.fromkeys(record[4][0] for record in records))


def is_public_address(address: str) -> bool:
    """Fail closed for non-global, reserved, mapped-private and scoped IPs."""
    if "%" in address:
        return False
    ip = ip_address(address)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return is_public_address(str(mapped))
    return ip.is_global and not any((
        ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast,
        ip.is_reserved, ip.is_unspecified,
    ))


@dataclass(frozen=True)
class DestinationDecision:
    allowed: bool
    rule: str
    reason: str
    host: str
    port: int
    scheme: str
    category: str | None = None
    resolved_ips: tuple[str, ...] = ()
    pinned_ip: str | None = None
    approval_required: bool = False


class DestinationPolicy:
    def __init__(self, config: EgressConfig, resolver: Resolver = resolve_host):
        self.config = config
        self.resolver = resolver
        self._run_grants: set[tuple[str, int, str]] = set()

    async def approve(self, host: str, port: int, scheme: str) -> bool:
        """Only a user-approved unknown public destination may gain a run grant."""
        decision = await self.evaluate(host, port, scheme)
        if not decision.approval_required:
            return False
        self._run_grants.add((decision.host, port, scheme))
        return True

    async def evaluate_url(self, url: str) -> DestinationDecision:
        try:
            host, port, scheme = parse_destination(url)
        except ValueError:
            # Do not echo URLs: paths, query strings and userinfo may be secret.
            return DestinationDecision(False, "invalid-url", "Malformed destination URL", "", 0, "")
        return await self.evaluate(host, port, scheme)

    async def evaluate(self, host: str, port: int, scheme: str = "https") -> DestinationDecision:
        try:
            host = normalize_host(host)
        except ValueError:
            return DestinationDecision(False, "suspicious-host", "Invalid or non-ASCII hostname", "", port, scheme)

        def deny(rule: str, reason: str, ips: tuple[str, ...] = (), *, approval: bool = False):
            return DestinationDecision(
                False, rule, reason, host, port, scheme,
                resolved_ips=ips, approval_required=approval,
            )

        if type(port) is not int or not 1 <= port <= 65535:
            return deny("port", "Invalid destination port")
        if scheme not in {"http", "https"}:
            return deny("scheme", "Only HTTP and HTTPS are supported")

        local = (host, port, scheme) in self.config.local_endpoints
        try:
            literal = ip_address(host)
        except ValueError:
            literal = None
        if literal is not None and not local:
            return deny("ip-literal", "Raw IP destinations are forbidden")
        explicit = host in self.config.provider_hosts or host in self.config.approved_sites
        if not local:
            if any(label.startswith("xn--") for label in host.split(".")) and not explicit:
                return deny("suspicious-host", "Punycode host is not explicitly allowlisted")
            if port not in self.config.remote_ports:
                # Preserve the TLS-required reason for ordinary remote http://.
                if not (scheme == "http" and port == 80):
                    return deny("port", "Remote port is not allowed")
            if scheme == "http":
                return deny("tls-required", "Remote destinations require HTTPS")

        try:
            addresses = (str(literal),) if literal else await asyncio.wait_for(
                self.resolver(host, port), timeout=self.config.dns_timeout,
            )
            ips = tuple(dict.fromkeys(str(ip_address(address)) for address in addresses))
            if not ips or any("%" in ip for ip in ips):
                return deny("dns-error", "No usable DNS addresses")
        except (OSError, ValueError, TimeoutError):
            return deny("dns-error", "Destination resolution failed")

        if local:
            if not all(ip_address(ip).is_loopback for ip in ips):
                return deny("private-ip", "Local model must resolve only to loopback", ips)
            category = "local-model"
        else:
            if not all(is_public_address(ip) for ip in ips):
                return deny("private-ip", "DNS returned a prohibited IP address", ips)
            if host in self.config.provider_hosts:
                category = "model-provider"
            elif host in self.config.approved_sites:
                category = "approved-site"
            elif (host, port, scheme) in self._run_grants:
                category = "approved-site"
            else:
                return deny("not-allowlisted", "Destination requires approval for this run", ips, approval=True)
        return DestinationDecision(
            True, category, "Destination permitted by policy", host, port, scheme,
            category=category, resolved_ips=ips, pinned_ip=ips[0],
        )
