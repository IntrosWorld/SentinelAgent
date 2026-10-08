"""Real headless browser checks against an offline, authenticated fixture."""

import asyncio
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from sentinel_agent.computer_use.browser import BrowserSession
from sentinel_agent.computer_use.schema import BrowserAction, BrowserFailure, StaleObservation
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.telemetry import read_verified


_HTML = b'''<!doctype html><html><head><title>Offline adaptive browser fixture</title></head><body>
<h1>Local search fixture</h1>
<form id="search-form" onsubmit="return false">
<input type="hidden" name="csrf_token" value="never-expose-this-csrf">
<input type="hidden" name="locale" value="en">
<label>Search query <input id="query" type="search"></label>
<button type="button" id="search" onclick="document.getElementById('result').textContent='Results for '+document.getElementById('query').value">Search</button>
</form>
<p id="result">No results yet</p>
<button id="hidden" style="display:none" onclick="window.unsafeClicked=true">Hidden control</button>
<div style="opacity:0"><button>Transparent control</button></div>
<label>Password <input id="password" type="password" value="never-expose-this-value"></label>
<label>Upload <input id="upload" type="file"></label>
<label>Card number <input id="credit-card"></label>
<label>API token <input id="token" value="never-expose-this-token"></label>
<input id="undisplayed" type="hidden" value="never-expose-this-hidden">
<button id="change" onclick="document.title='Changed fixture'">Change title</button>
<div style="height:1600px">Scroll content</div><p>End of page</p>
</body></html>'''


@pytest.fixture
def browser_executable():
    if importlib.util.find_spec("playwright") is None:
        pytest.skip("Requires the optional browser extra")
    for executable in (
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path("/usr/bin/google-chrome"), Path("/usr/bin/chromium"),
    ):
        if executable.exists():
            return executable
    pytest.skip("Requires installed Chrome, Edge or Chromium")


@pytest.fixture
def browser_url():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_HTML)))
            self.end_headers()
            self.wfile.write(_HTML)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
async def browser_session(tmp_path, browser_url, browser_executable):
    from urllib.parse import urlsplit
    port = urlsplit(browser_url).port

    async def resolver(host, port):
        if host != "127.0.0.1":
            raise OSError("The offline fixture has no external DNS")
        return ["127.0.0.1"]

    policy = DestinationPolicy(EgressConfig(
        local_endpoints=frozenset({("127.0.0.1", port, "http")}),
        approved_sites=frozenset(),
    ), resolver)
    gateway = EgressGateway(policy=policy, audit_root=tmp_path).start()
    session = BrowserSession(gateway, browser_executable)
    try:
        await session.start(browser_url)
        yield session
    finally:
        await session.close()
        gateway.close()


async def _page_eval(session, script):
    async def evaluate():
        return await session._page.evaluate(script)
    return await asyncio.wrap_future(session.gateway.submit(evaluate()))


def _element(snapshot, name):
    return next(item for item in snapshot.elements if item.name == name)


async def test_observe_fill_click_and_scroll(browser_session):
    session = browser_session
    first = await session.observe()
    assert first.title == "Offline adaptive browser fixture"
    assert "Local search fixture" in first.text
    assert "never-expose" not in first.model_dump_json()
    assert len(first.text) <= 12000 and len(first.elements) <= 60
    assert not {"Password", "Upload", "Card number", "API token", "Hidden control", "Transparent control"}.intersection(
        item.name for item in first.elements)
    field = _element(first, "Search query")
    assert field.editable
    fill = BrowserAction(kind="fill", snapshot_id=first.id, element_id=field.id, text="hello world")
    await session.validate(fill)
    await session.act(fill)
    second = await session.observe()
    assert second.id != first.id and _element(second, "Search query").id != field.id
    assert _element(second, "Search query").value == "hello world"
    await session.act(BrowserAction(kind="click", snapshot_id=second.id, element_id=_element(second, "Search").id))
    third = await session.observe()
    assert "Results for hello world" in third.text
    await session.act(BrowserAction(kind="scroll", snapshot_id=third.id))
    assert await _page_eval(session, "window.scrollY") > 0
    records = read_verified(session.gateway.audit.path)
    assert any(row.get("kind") == "connection" and row.get("decision") == "allow" for row in records)
    assert any(row.get("method") == "BROWSER_FILL" for row in records)


@pytest.mark.parametrize("mutation", [
    "document.getElementById('search').textContent='Replaced purpose'",
    "document.getElementById('search').outerHTML='<button id=search>Search</button>'",
    "document.getElementById('search').disabled=true",
    "document.getElementById('query').value='changed-without-a-mutation-event'",
    "history.pushState({}, '', '/different-page')",
])
async def test_stale_state_never_dispatches(browser_session, mutation):
    session = browser_session
    snapshot = await session.observe()
    action = BrowserAction(kind="click", snapshot_id=snapshot.id, element_id=_element(snapshot, "Search").id)
    await session.validate(action)
    await _page_eval(session, mutation)
    with pytest.raises(StaleObservation):
        await session.act(action)
    assert await _page_eval(session, "document.getElementById('result').textContent") == "No results yet"


async def test_reload_invalidates_observation_before_dispatch(browser_session):
    session = browser_session
    snapshot = await session.observe()

    async def reload():
        await session._page.reload(wait_until="domcontentloaded")

    await asyncio.wrap_future(session.gateway.submit(reload()))
    with pytest.raises(StaleObservation):
        await session.act(BrowserAction(kind="click", snapshot_id=snapshot.id, element_id=_element(snapshot, "Search").id))
    assert await _page_eval(session, "document.getElementById('result').textContent") == "No results yet"


async def test_forged_and_expired_ids_are_rejected(browser_session):
    session = browser_session
    snapshot = await session.observe()
    with pytest.raises(StaleObservation):
        await session.act(BrowserAction(kind="click", snapshot_id=snapshot.id, element_id="#search"))
    await session.observe()
    with pytest.raises(StaleObservation):
        await session.act(BrowserAction(kind="click", snapshot_id=snapshot.id, element_id=_element(snapshot, "Search").id))
    assert await _page_eval(session, "document.getElementById('result').textContent") == "No results yet"


async def test_dlp_blocks_secret_and_redacts_email_before_input(browser_session):
    session = browser_session
    snapshot = await session.observe()
    field = _element(snapshot, "Search query")
    secret = "sk-" + "a" * 30
    with pytest.raises(BrowserFailure, match="DLP"):
        await session.act(BrowserAction(kind="fill", snapshot_id=snapshot.id, element_id=field.id, text=secret))
    assert await _page_eval(session, "document.getElementById('query').value") == ""
    await session.act(BrowserAction(kind="fill", snapshot_id=snapshot.id, element_id=field.id, text="person@example.com"))
    assert await _page_eval(session, "document.getElementById('query').value") == "[REDACTED:email]"
    assert secret not in session.gateway.audit.path.read_text()
    assert "person@example.com" not in session.gateway.audit.path.read_text()


async def test_closed_session_does_not_close_shared_browser(browser_session, browser_url):
    session = browser_session
    second = BrowserSession(session.gateway, session.executable)
    await second.start(browser_url)
    try:
        await session.close()
        assert "Local search fixture" in (await second.observe()).text
        assert session.gateway.proxy.server is not None
    finally:
        await second.close()


@pytest.mark.parametrize("hidden_name", ["csrf_token", "csrfmiddlewaretoken", "__RequestVerificationToken", "locale"])
async def test_hidden_bookkeeping_does_not_disable_search_form(browser_session, hidden_name):
    session = browser_session
    await _page_eval(session, """() => {
      const form = document.createElement('form');
      form.onsubmit = () => false;
      form.innerHTML = '<label>Login user<input></label><input type="password"><button>Sign in</button>';
      document.body.appendChild(form);
    }""")
    async def change_name():
        await session._page.eval_on_selector('#search-form input[type=hidden]', '(el, name) => el.name = name', hidden_name)
    await asyncio.wrap_future(session.gateway.submit(change_name()))
    snapshot = await session.observe()
    assert not {"Login user", "Sign in"}.intersection(item.name for item in snapshot.elements)
    assert "never-expose-this-csrf" not in snapshot.model_dump_json()
    await session.act(BrowserAction(kind="fill", snapshot_id=snapshot.id,
                                   element_id=_element(snapshot, "Search query").id, text="hello world"))
    snapshot = await session.observe()
    await session.act(BrowserAction(kind="click", snapshot_id=snapshot.id,
                                   element_id=_element(snapshot, "Search").id))
    assert "Results for hello world" in (await session.observe()).text


async def test_external_associated_password_field_blocks_form(browser_session):
    session = browser_session
    await _page_eval(session, """() => {
      const input = document.createElement('input');
      input.type = 'password'; input.setAttribute('form', 'search-form');
      document.body.appendChild(input);
    }""")
    snapshot = await session.observe()
    assert not {"Search query", "Search"}.intersection(item.name for item in snapshot.elements)


async def test_external_submit_button_for_credential_form_is_excluded(browser_session):
    session = browser_session
    await _page_eval(session, """() => {
      const form = document.createElement('form'); form.id = 'credentials';
      form.innerHTML = '<input type=hidden name=api_key value=test-only-not-a-real-key>';
      const button = document.createElement('button'); button.setAttribute('form', 'credentials');
      button.textContent = 'External submit';
      document.body.append(form, button);
    }""")
    assert "External submit" not in {item.name for item in (await session.observe()).elements}


async def test_links_expose_only_web_or_same_page_destinations(browser_session):
    session = browser_session
    await _page_eval(session, """() => {
      const links = {Email:'mailto:person@example.test', Telephone:'tel:123',
        Custom:'some-desktop-app:open', Script:'javascript:window.unsafeClicked=true',
        Data:'data:text/html,test', FTP:'ftp://example.test/test',
        Anchor:'#result', Web:'https://example.test/', Relative:'/same-site'};
      for (const [name, href] of Object.entries(links)) {
        const link = document.createElement('a'); link.textContent=name; link.href=href;
        document.body.appendChild(link);
      }
    }""")
    snapshot = await session.observe()
    names = {item.name for item in snapshot.elements}
    assert {"Anchor", "Web", "Relative"} <= names
    assert not {"Email", "Telephone", "Custom", "Script", "Data", "FTP"}.intersection(names)
    action = BrowserAction(kind="click", snapshot_id=snapshot.id, element_id=_element(snapshot, "Anchor").id)
    await _page_eval(session, """() => {
      const anchor = [...document.querySelectorAll('a')].find(el => el.textContent === 'Anchor');
      anchor.href = 'javascript:window.unsafeClicked=true';
    }""")
    with pytest.raises(StaleObservation):
        await session.act(action)
    assert await _page_eval(session, "!!window.unsafeClicked") is False


async def test_non_web_form_actions_are_excluded(browser_session):
    session = browser_session
    await _page_eval(session, """() => {
      const form = document.createElement('form');
      form.action = 'mailto:person@example.test';
      form.innerHTML = '<label>Mail message<input></label><button>Send mail</button>';
      document.body.appendChild(form);
      const override = document.createElement('button'); override.setAttribute('form', 'search-form');
      override.setAttribute('formaction', 'javascript:window.unsafeClicked=true');
      override.textContent = 'Script submit'; document.body.appendChild(override);
    }""")
    names = {item.name for item in (await session.observe()).elements}
    assert not {"Mail message", "Send mail", "Script submit"}.intersection(names)
