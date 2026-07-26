import pytest

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.llm import OllamaPlanner
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import Action, RunStatus


def build_agent() -> SentinelAgent:
    model = OllamaPlanner("http://127.0.0.1:1", "unused", timeout=0.01)
    return SentinelAgent(HybridPlanner(model))


@pytest.mark.asyncio
async def test_fallback_completes_report_without_model():
    run = await build_agent().start("Check attendance and create a report", use_model=False)
    assert run.status == RunStatus.COMPLETED
    assert run.result["shortages"] == {"AIML": 68, "OS": 73}


@pytest.mark.asyncio
async def test_email_requires_approval():
    agent = build_agent()
    run = await agent.start("Check attendance and send the report by email", use_model=False)
    assert run.status == RunStatus.WAITING_APPROVAL
    assert run.pending_action.name == "send_email"
    run = agent.approve(run.id, True)
    assert run.status == RunStatus.COMPLETED


def test_forbidden_action_is_blocked():
    decision = build_agent().policy.evaluate(
        Action(name="enter_password", expected_result="Password entered")
    )
    assert not decision.allowed


@pytest.mark.asyncio
async def test_popup_disturbance_recovers():
    run = await build_agent().start(
        "Check attendance and create a report", use_model=False, disturbance="popup"
    )
    assert run.status == RunStatus.COMPLETED
    assert any("popup" in event.message.lower() for event in run.events)

