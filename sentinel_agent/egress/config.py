"""Immutable policy configuration derived from operator-owned model profiles."""

from dataclasses import dataclass
from ipaddress import ip_address
import re
from urllib.parse import urlsplit

from sentinel_agent.models import ModelProfile


def normalize_host(host: str) -> str:
    """Accept an IP or ASCII DNS name; never interpret URLs or authorities here."""
    if not host or host != host.strip() or not host.isascii() or "%" in host:
        raise ValueError("Invalid destination host")
    try:
        return str(ip_address(host))
    except ValueError:
        pass
    host = host.lower().removesuffix(".")
    labels = host.split(".")
    if len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError("Invalid destination host")
    return host


def parse_destination(url: str) -> tuple[str, int, str]:
    """Validate before urlsplit can silently discard control characters."""
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError("Invalid destination URL")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("An HTTP or HTTPS URL is required")
    if parts.username is not None or parts.password is not None:
        raise ValueError("URL credentials are not permitted")
    host = normalize_host(parts.hostname)
    port = parts.port
    if parts.netloc.endswith(":"):
        raise ValueError("Empty destination port")
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise ValueError("Invalid destination port")
    return host, port, parts.scheme


def is_local_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class EgressConfig:
    # Exact scheme/host/port grants prevent a local model exemption exposing
    # other services on the machine. No suffix matching or wildcard hosts.
    local_endpoints: frozenset[tuple[str, int, str]] = frozenset()
    provider_hosts: frozenset[str] = frozenset()
    approved_sites: frozenset[str] = frozenset({"www.youtube.com"})
    remote_ports: frozenset[int] = frozenset({443})
    dns_timeout: float = 5.0

    def __post_init__(self) -> None:
        if self.dns_timeout <= 0:
            raise ValueError("DNS timeout must be positive")
        if any(type(port) is not int or not 1 <= port <= 65535 for port in self.remote_ports):
            raise ValueError("Invalid allowed port")
        for host in self.provider_hosts | self.approved_sites:
            if normalize_host(host) != host:
                raise ValueError("Allowlisted hosts must be canonical")
        for host, port, scheme in self.local_endpoints:
            if (normalize_host(host) != host or not is_local_host(host)
                    or type(port) is not int or not 1 <= port <= 65535
                    or scheme not in {"http", "https"}):
                raise ValueError("Invalid local model endpoint")

    @classmethod
    def from_profiles(
        cls, profiles: list[ModelProfile], *, approved_sites: tuple[str, ...] = ("www.youtube.com",)
    ) -> "EgressConfig":
        local = set()
        providers = set()
        for profile in profiles:
            host, port, scheme = parse_destination(profile.base_url)
            if is_local_host(host):
                local.add((host, port, scheme))
            else:
                providers.add(host)
        return cls(
            local_endpoints=frozenset(local),
            provider_hosts=frozenset(providers),
            approved_sites=frozenset(normalize_host(host) for host in approved_sites),
        )
