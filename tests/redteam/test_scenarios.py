import pytest

from sentinel_agent.egress.redteam import evaluate


@pytest.fixture(scope="module")
def results():
    import asyncio
    return {row["id"]: row for row in asyncio.run(evaluate())["scenarios"]}


@pytest.mark.parametrize("scenario", [f"A{i}" for i in range(1, 15)] + [f"L{i}" for i in range(1, 5)])
def test_redteam_scenario(results, scenario):
    assert results[scenario]["passed"], results[scenario]
