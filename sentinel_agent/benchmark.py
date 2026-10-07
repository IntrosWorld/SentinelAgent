import argparse
import asyncio
import json
from pathlib import Path

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.config import get_settings
from sentinel_agent.llm import planner_from_profile
from sentinel_agent.models import ModelRegistry
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import RunStatus


async def evaluate(use_model: bool) -> dict:
    settings = get_settings()
    model = planner_from_profile(ModelRegistry(settings.models_file).get())
    agent = SentinelAgent(HybridPlanner(model), settings.max_steps)
    tasks = json.loads((Path(__file__).parents[1] / "benchmark" / "tasks.json").read_text())
    results = []
    for task in tasks:
        run = await agent.start(task["instruction"], use_model, task.get("disturbance"), False)
        if run.status == RunStatus.WAITING_APPROVAL:
            run = agent.approve(run.id, task.get("approve", False))
        expected = task.get("expected_status", RunStatus.COMPLETED)
        results.append({"id": task["id"], "passed": run.status == expected, "status": run.status, "expected": expected})
    passed = sum(result["passed"] for result in results)
    agent.close()
    return {"passed": passed, "total": len(results), "success_rate": passed / len(results), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="store_true", help="Use the active model profile")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.model)), indent=2))


if __name__ == "__main__":
    main()
