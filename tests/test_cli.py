from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel_agent.cli import async_main, build_parser
from sentinel_agent.schemas import RunStatus


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


@pytest.mark.parametrize("json_output,terminal,waits", [(False, True, True), (True, True, False), (False, False, False)])
async def test_managed_browser_waits_only_in_interactive_terminal(monkeypatch, json_output, terminal, waits):
    agent = MagicMock()
    agent.gateways = {"browser-run": SimpleNamespace(browser=object())}
    monkeypatch.setattr("sentinel_agent.cli.make_agent", lambda *args: (agent, None, None))
    monkeypatch.setattr("sentinel_agent.cli.execute", AsyncMock(return_value=SimpleNamespace(id="browser-run", status=RunStatus.COMPLETED)))
    monkeypatch.setattr("sentinel_agent.cli.sys.stdin.isatty", lambda: terminal)
    prompt = MagicMock(return_value="")
    monkeypatch.setattr("builtins.input", prompt)
    args = Namespace(task="open chrome", no_model=True, disturbance=None, live_desktop=True, json=json_output)
    assert await async_main(args) == 0
    assert prompt.called is waits
    agent.close.assert_called_once()
