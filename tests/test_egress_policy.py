import asyncio
import socket

import pytest

from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy, is_public_address
from sentinel_agent.models import DEFAULT_PROFILES, ModelProfile


PUBLIC = "8.8.8.8"


class FakeResolver:
    def __init__(self, answers=(PUBLIC,)):
        self.answers = answers
        self.calls = []

    async def __call__(self, host, port):
        self.calls.append((host, port))
        return self.answers


@pytest.fixture
def config():
    return EgressConfig.from_profiles(DEFAULT_PROFILES)


@pytest.mark.parametrize("url,rule", [
    ("https://169.254.169.254", "ip-literal"),
    ("https://8.8.8.8", "ip-literal"),
    ("https://[::1]", "ip-literal"),
    ("https://[::ffff:127.0.0.1]", "ip-literal"),
    ("https://xn--openai-fake.com", "suspicious-host"),
    ("https://api.openai.com:8443", "port"),
    ("https://api.openai.com:22", "port"),
    ("http://api.openai.com", "tls-required"),
    ("http://api.openai.com:443", "tls-required"),
    ("http://api.openai.com:8443", "port"),
    ("http://127.0.0.1:9999", "ip-literal"),
    ("https://127.0.0.1:11434", "ip-literal"),
])
async def test_reject_before_dns(config, url, rule):
    resolver = FakeResolver()
    decision = await DestinationPolicy(config, resolver).evaluate_url(url)
    assert not decision.allowed
    assert decision.rule == rule
    assert not decision.approval_required
    assert decision.pinned_ip is None
    assert resolver.calls == []


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1",
    "169.254.169.254", "224.0.0.1", "240.0.0.1", "0.0.0.0",
    "100.64.0.1", "192.0.2.1", "::1", "::", "fc00::1",
    "fe80::1", "ff02::1", "2001:db8::1", "::ffff:127.0.0.1",
])
async def test_any_nonpublic_answer_blocks_even_allowlisted_host(config, ip):
    assert not is_public_address(ip)
    result = await DestinationPolicy(config, FakeResolver((PUBLIC, ip))).evaluate_url(
        "https://api.openai.com/v1/chat/completions"
    )
    assert result.rule == "private-ip"
    assert not result.allowed
    assert result.pinned_ip is None
    assert not result.approval_required


@pytest.mark.parametrize("url,category", [
    ("https://api.openai.com/v1", "model-provider"),
    ("https://api.anthropic.com/v1/messages", "model-provider"),
    ("https://generativelanguage.googleapis.com/v1beta", "model-provider"),
    ("https://www.youtube.com/results?search_query=hello", "approved-site"),
    ("https://API.OPENAI.COM./v1", "model-provider"),
])
async def test_approved_public_hosts_pin_checked_address(config, url, category):
    resolver = FakeResolver((PUBLIC, "2606:4700:4700::1111", PUBLIC))
    result = await DestinationPolicy(config, resolver).evaluate_url(url)
    assert result.allowed and result.category == category
    assert result.rule == category
    assert result.pinned_ip == PUBLIC
    assert result.resolved_ips == (PUBLIC, "2606:4700:4700::1111")
    assert len(resolver.calls) == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/api/generate", "http://127.0.0.1:1234/v1"])
async def test_configured_local_models_allow_http_without_dns(config, url):
    resolver = FakeResolver()
    result = await DestinationPolicy(config, resolver).evaluate_url(url)
    assert result.allowed and result.category == "local-model"
    assert result.pinned_ip == "127.0.0.1"
    assert resolver.calls == []


async def test_localhost_profile_still_checks_all_dns_answers():
    profile = ModelProfile(name="local", provider="ollama", model="test", base_url="http://localhost:11434")
    config = EgressConfig.from_profiles([profile])
    good = await DestinationPolicy(config, FakeResolver(("::1", "127.0.0.1"))).evaluate_url(profile.base_url)
    bad = await DestinationPolicy(config, FakeResolver(("127.0.0.1", PUBLIC))).evaluate_url(profile.base_url)
    assert good.allowed and good.pinned_ip == "::1"
    assert not bad.allowed and bad.rule == "private-ip"


@pytest.mark.parametrize("host", ["attacker.example", "api.openai.com.attacker.example", "sub.api.openai.com"])
async def test_unknown_public_host_requires_approval_but_is_not_allowed(config, host):
    result = await DestinationPolicy(config, FakeResolver()).evaluate(host, 443)
    assert not result.allowed
    assert result.approval_required and result.rule == "not-allowlisted"
    assert result.pinned_ip is None


async def test_dns_rebinding_decision_retains_first_validated_address(config):
    calls = 0

    async def rebinding(host, port):
        nonlocal calls
        calls += 1
        return [PUBLIC] if calls == 1 else ["127.0.0.1"]

    policy = DestinationPolicy(config, rebinding)
    first = await policy.evaluate("api.openai.com", 443)
    assert calls == 1 and first.pinned_ip == PUBLIC
    second = await policy.evaluate("api.openai.com", 443)
    assert second.rule == "private-ip" and not second.allowed
    assert first.pinned_ip == PUBLIC


@pytest.mark.parametrize("answers", [(), ("not-an-ip",), ("fe80::1%eth0",)])
async def test_bad_dns_data_denied(config, answers):
    result = await DestinationPolicy(config, FakeResolver(answers)).evaluate("api.openai.com", 443)
    assert not result.allowed and result.rule == "dns-error"


async def test_dns_failure_and_timeout_are_closed():
    async def failure(host, port):
        raise socket.gaierror("synthetic resolver failure")

    async def slow(host, port):
        await asyncio.sleep(1)
        return [PUBLIC]

    config = EgressConfig(dns_timeout=0.01)
    for resolver in (failure, slow):
        result = await DestinationPolicy(config, resolver).evaluate("example.com", 443)
        assert result.rule == "dns-error" and not result.allowed


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "https://user:secret@api.openai.com",
    "https://api.openai.com:0", "https://api.openai.com:65536",
    "https://api.openai.com:", "https://api.openai.com:abc",
    "https://api.openai.com\n", " https://api.openai.com",
    "https://api.openai.com\\@evil.com", "https://bad..example",
    "https://-bad.example", "https://api.openаi.com", "https://[::1",
    "https://[fe80::1%25eth0]", "https://api%2eopenai.com",
])
async def test_malformed_urls_do_not_leak_input_or_resolve(config, url):
    resolver = FakeResolver()
    result = await DestinationPolicy(config, resolver).evaluate_url(url)
    assert result.rule == "invalid-url" and not result.allowed
    assert result.host == "" and "secret" not in result.reason
    assert resolver.calls == []


async def test_explicit_punycode_allowlist_still_requires_public_dns():
    config = EgressConfig(approved_sites=frozenset({"xn--bcher-kva.example"}))
    public = await DestinationPolicy(config, FakeResolver()).evaluate("xn--bcher-kva.example", 443)
    private = await DestinationPolicy(config, FakeResolver(("10.0.0.1",))).evaluate("xn--bcher-kva.example", 443)
    assert public.allowed and public.category == "approved-site"
    assert private.rule == "private-ip"


def test_malformed_profiles_fail_configuration():
    profile = ModelProfile(name="bad", provider="ollama", model="test", base_url="http://user:secret@localhost")
    with pytest.raises(ValueError, match="credentials"):
        EgressConfig.from_profiles([profile])
    with pytest.raises(ValueError, match="local model"):
        EgressConfig(local_endpoints=frozenset({("8.8.8.8", 80, "http")}))
