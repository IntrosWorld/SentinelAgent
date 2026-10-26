import asyncio
import json
from pathlib import Path

import pytest

from sentinel_agent.desktop_service import DesktopService, StartRun, credential_name, validated_profile


@pytest.fixture
def service(tmp_path):
    events = []
    value = DesktopService(tmp_path, events.append)
    value.events = events
    return value


def profile(**kwargs):
    return {"name": "mine", "provider": "ollama", "model": "qwen3-vl:2b-instruct",
            "base_url": "http://localhost:11434", "vision": True, **kwargs}


def test_profile_canonicalizes_endpoint_and_owns_key_namespace():
    result = validated_profile(profile(api_key_env="PATH"))
    assert result.base_url == "http://127.0.0.1:11434"
    assert result.api_key_env == credential_name("mine")


@pytest.mark.parametrize("url", ["http://example.com", "http://user:secret@localhost:11434",
    "http://localhost:11434?key=secret", "http://localhost:11434#secret", "file:///x", "http://localhost\n:11434"])
def test_bad_endpoints_rejected(url):
    with pytest.raises(ValueError):
        validated_profile(profile(base_url=url))


@pytest.mark.parametrize("patch", [{"timeout": 901}, {"name": "x" * 81}, {"model": "x" * 201}, {"secret": "x"}])
def test_profile_limits(patch):
    with pytest.raises(ValueError):
        validated_profile(profile(**patch))


async def test_save_and_reload_profiles_without_secret(service):
    await service.dispatch("models.save", profile())
    await service.dispatch("credentials.set", {"name": "mine", "key": "private-secret"})
    assert "private-secret" not in service.registry.path.read_text()
    assert service.registry.get().name == "mine"
    assert service.registry.get().api_key == "private-secret"


async def test_unknown_method_is_not_a_shell(service):
    with pytest.raises(ValueError):
        await service.dispatch("exec", {"command": "calc.exe"})


async def test_approval_requires_matching_task_and_one_use_token(service):
    service.run_id = "run1"
    waiting = asyncio.create_task(service.approval({"action": "click"}))
    await asyncio.sleep(0)
    token = service.pending[0]
    for args in [{"run_id": "wrong", "token": token, "approved": True},
                 {"run_id": "run1", "token": "old", "approved": True},
                 {"run_id": "run1", "token": token, "approved": "true"}]:
        with pytest.raises(ValueError):
            await service.dispatch("run.approve", args)
    good = {"run_id": "run1", "token": token, "approved": False}
    await service.dispatch("run.approve", good)
    with pytest.raises(ValueError):
        await service.dispatch("run.approve", good)
    assert await waiting is False
    assert service.pending is None


async def test_cancelling_approval_clears_it(service):
    pending = asyncio.create_task(service.approval({}))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert service.pending is None


async def test_configuration_cannot_change_during_run(service):
    service.running = asyncio.create_task(asyncio.sleep(100))
    with pytest.raises(ValueError):
        await service.dispatch("models.save", profile())
    await service.close()


async def test_cloud_observation_consent_required_before_start(service):
    await service.dispatch("models.save", profile(base_url="https://example.com", provider="openai_compatible"))
    with pytest.raises(Exception):
        await service.dispatch("run.start", {"goal": "Click square", "window_id": 123, "profile": "mine"})
    assert service.running is None


async def test_demo_rejects_arbitrary_files_or_actions(service):
    with pytest.raises(ValueError):
        await service.dispatch("run.start", {"mode": "demo", "goal": "create report.pdf"})


async def test_remote_discovery_refused(service):
    with pytest.raises(ValueError):
        await service.dispatch("models.discover", {"provider": "ollama", "base_url": "https://example.com"})


def test_interrupted_history_is_not_resumable(tmp_path):
    (tmp_path / "history.json").write_text(json.dumps([{"id": "old", "status": "running"}]))
    service = DesktopService(tmp_path, lambda _: None)
    assert service.history[0]["status"] == "interrupted"


def test_run_options_are_strict():
    with pytest.raises(ValueError):
        StartRun(goal="Do task", single_step="false")


@pytest.mark.parametrize("contents", ['{}', '[null,"bad",3]', 'not json'])
def test_corrupt_history_does_not_break_startup(tmp_path, contents):
    (tmp_path / "history.json").write_text(contents)
    assert DesktopService(tmp_path, lambda _: None).history == []


async def test_desktop_bridge_rejects_simulator_mode(service):
    with pytest.raises(ValueError):
        await service.dispatch("run.start", {"mode": "demo", "goal": "Check attendance and create a report"})
    assert service.running is None
    assert service.events == []
