from argparse import Namespace

import pytest

from sentinel_agent.cli import async_main, build_parser


def test_cli_parser_accepts_direct_task():
    args = build_parser().parse_args(["Check attendance and create a report", "--no-model", "--json"])
    assert args.task.startswith("Check attendance")
    assert args.no_model
    assert args.json


@pytest.mark.asyncio
async def test_noninteractive_cli_completes(capsys):
    args = Namespace(
        task="Check attendance and create a report",
        no_model=True,
        disturbance=None,
        live_desktop=False,
        json=True,
    )
    exit_code = await async_main(args)
    output = capsys.readouterr().out
    assert exit_code == 0
    assert '"status": "completed"' in output
    assert '"AIML": 68' in output
