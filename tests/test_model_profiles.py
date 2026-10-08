import json

import httpx
import pytest

from sentinel_agent.llm import ProviderPlanner
from sentinel_agent.models import ModelProfile, ModelRegistry
from sentinel_agent.egress.client import EgressGateway


def test_registry_switches_profiles_without_storing_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_TEST_KEY", "do-not-write-this")
    registry = ModelRegistry(tmp_path / "models.json")
    registry.add(ModelProfile(name="test-api", provider="openai_compatible", model="test-model", base_url="http://localhost:9999/v1", api_key_env="PRIVATE_TEST_KEY"))
    registry.use("test-api")
    saved = (tmp_path / "models.json").read_text(encoding="utf-8")
    assert registry.get().name == "test-api"
    assert "PRIVATE_TEST_KEY" in saved
    assert "do-not-write-this" not in saved


@pytest.mark.parametrize("mode", ["auto", "pixels", "normalized_1000"])
def test_registry_preserves_explicit_desktop_coordinate_mode(tmp_path, mode):
    registry = ModelRegistry(tmp_path / "models.json")
    registry.add(ModelProfile(name="vision-test", provider="ollama", model="test-model",
                              base_url="http://127.0.0.1:11434", vision=True, desktop_coordinates=mode))
    loaded = registry.get("vision-test")
    assert loaded.desktop_coordinates == mode
    assert registry.get().name == "local-ollama"


def test_legacy_profiles_load_without_desktop_coordinate_metadata():
    legacy = ModelProfile.model_validate({"name": "legacy", "provider": "ollama", "model": "model",
                                         "base_url": "http://127.0.0.1:11434"})
    assert legacy.desktop_coordinates == "auto"


@pytest.mark.parametrize("mode", ["normalized", "normalized_1", 1000, None, True])
def test_invalid_desktop_coordinate_modes_are_rejected(mode):
    with pytest.raises(ValueError):
        ModelProfile(name="invalid", provider="ollama", model="model", base_url="http://127.0.0.1:11434",
                     desktop_coordinates=mode)


@pytest.mark.asyncio
@pytest.mark.parametrize(("provider", "response_body"), [
    ("openai_compatible", {"choices": [{"message": {"content": '{"goal":"demo","actions":[]}'}}]}),
    ("anthropic", {"content": [{"type": "text", "text": '{"goal":"demo","actions":[]}'}]}),
    ("gemini", {"candidates": [{"content": {"parts": [{"text": '{"goal":"demo","actions":[]}'}]}}]}),
])
async def test_provider_adapters_parse_plans(monkeypatch, tmp_path, provider, response_body):
    seen = {}
    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=response_body)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    base_url = "https://example.test/v1" if provider != "anthropic" else "https://example.test"
    profile = ModelProfile(name="test", provider=provider, model="model-id", base_url=base_url)
    # Only test wire-format parsing here; gateway integration tests cover policy.
    gateway = EgressGateway(enabled=False, audit_root=tmp_path).start()
    try:
        with gateway.activate():
            plan = await ProviderPlanner(profile).create_plan("demo", {}, {})
    finally:
        gateway.close()
    assert plan.goal == "demo"
    assert plan.actions == []
    if provider != "gemini":
        assert seen["body"]["model"] == "model-id"


@pytest.mark.asyncio
async def test_missing_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv("MISSING_SENTINEL_TEST_KEY", raising=False)
    planner = ProviderPlanner(ModelProfile(name="secured", provider="openai_compatible", model="model-id", base_url="https://example.test/v1", api_key_env="MISSING_SENTINEL_TEST_KEY"))
    health = await planner.health()
    assert health["online"] is False
    assert "MISSING_SENTINEL_TEST_KEY" in health["error"]
