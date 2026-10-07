from unittest.mock import Mock, patch

import pytest

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.llm import OllamaPlanner
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import RunStatus


def build_agent() -> SentinelAgent:
    # Launch contract tests mock the OS; gateway behavior has separate offline tests.
    return SentinelAgent(HybridPlanner(OllamaPlanner("http://127.0.0.1:1", "unused")), egress_enabled=False)


@pytest.mark.asyncio
async def test_youtube_search_is_simulated_by_default():
    run = await build_agent().start(
        "Open Chrome, open YouTube, and search hello world", use_model=False
    )
    assert run.status == RunStatus.COMPLETED
    environment = build_agent  # keep assertion output compact on failure
    assert run.plan.actions[0].arguments == {"app": "chrome"}
    assert run.plan.actions[1].arguments == {"query": "hello world"}
    assert "Simulated" in run.events[-2].message


@pytest.mark.asyncio
async def test_live_desktop_uses_allowlisted_executables():
    agent = build_agent()
    process = Mock()
    with patch("sentinel_agent.desktop.subprocess.Popen", return_value=process) as popen:
        run = await agent.start(
            "Open Chrome, open YouTube, and search hello world",
            use_model=False,
            live_desktop=True,
        )
    assert run.status == RunStatus.COMPLETED
    assert popen.call_count == 2
    second_command = popen.call_args_list[1].args[0]
    assert second_command[0].lower().endswith("chrome.exe")
    assert "youtube.com/results?search_query=hello+world" in second_command[1]
