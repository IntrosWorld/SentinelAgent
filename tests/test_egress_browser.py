import importlib.util
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from sentinel_agent.egress.browser import ManagedBrowser
from sentinel_agent.egress.client import EgressBlocked, EgressError, EgressGateway
from sentinel_agent.egress.config import EgressConfig
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.telemetry import read_verified
from sentinel_agent.desktop import DesktopEnvironment


async def test_browser_credentials_are_in_memory_and_sessions_reused(tmp_path):
    gateway = EgressGateway(audit_root=tmp_path).start()
    manager = ManagedBrowser(gateway)
    runtime = MagicMock()
    browser, context, page = AsyncMock(), AsyncMock(), AsyncMock()
    page.is_closed = MagicMock(return_value=False)
    page.url = "about:blank"
    page.title.return_value = ""
    browser.new_context.return_value = context
    context.new_page.return_value = page
    runtime.chromium.launch = AsyncMock(return_value=browser)
    runtime.stop = AsyncMock()
    manager.runtime = runtime
    try:
        await manager.open("chrome.exe", headless=True)
        await manager.open("chrome.exe", headless=True)
        options = runtime.chromium.launch.call_args.kwargs
        assert options["proxy"]["password"] == gateway.proxy.token
        assert gateway.proxy.token not in str(options["args"])
        assert "--proxy-bypass-list=<-loopback>" in options["args"]
        assert runtime.chromium.launch.await_count == 1
        assert context.route.await_count == 1
        assert browser.new_context.call_args.kwargs["service_workers"] == "block"
        with pytest.raises(EgressBlocked):
            await manager.open("chrome.exe", "https://169.254.169.254")
        route = AsyncMock()
        route.request.url = "https://169.254.169.254"
        await manager._route(route)
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_called()
        page.url = "http://127.0.0.1:11434/"
        page.goto.return_value = MagicMock(status=200)
        ready = AsyncMock()
        page.locator = MagicMock(return_value=MagicMock(first=ready))
        await manager.open("chrome.exe", page.url, headless=True, ready_selector="#result")
        ready.wait_for.assert_awaited_once_with(state="visible", timeout=15000)
        page.goto.side_effect = RuntimeError(gateway.proxy.token)
        with pytest.raises(EgressError) as failure:
            await manager.open("chrome.exe", page.url, headless=True)
        assert gateway.proxy.token not in str(failure.value)
    finally:
        await manager.close()
        gateway.close()
    context.close.assert_awaited_once()
    browser.close.assert_awaited_once()
    runtime.stop.assert_awaited_once()


@pytest.mark.parametrize("executable", [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
])
def test_real_browser_authenticates_and_does_not_leak_proxy_header(tmp_path, executable):
    if not executable.exists() or importlib.util.find_spec("playwright") is None:
        pytest.skip("Requires installed Chrome/Edge and the browser extra")
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(dict(self.headers))
            body = b"<html><head><title>Sentinel authenticated proxy</title></head><body>hello world</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    async def resolver(host, port):
        return ["127.0.0.1"] if host == "127.0.0.1" else ["8.8.8.8"]
    policy = DestinationPolicy(EgressConfig(local_endpoints=frozenset({("127.0.0.1", port, "http")})), resolver)
    gateway = EgressGateway(policy=policy, audit_root=tmp_path).start()
    try:
        result = gateway.open_browser(executable, f"http://127.0.0.1:{port}/", headless=True)
        assert result["title"] == "Sentinel authenticated proxy"
        assert result["status"] == 200
        with pytest.raises(EgressBlocked):
            gateway.open_browser(executable, "https://169.254.169.254", headless=True)
        assert hits
        assert all("proxy-authorization" not in {k.lower() for k in h} for h in hits)
    finally:
        gateway.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    records = read_verified(gateway.audit.path)
    assert any(r.get("decision") == "allow" and r.get("port") == port and r.get("kind") == "connection" for r in records)
    assert gateway.proxy.token not in gateway.audit.path.read_text()
    assert gateway.browser is None


@pytest.mark.skipif(os.environ.get("SENTINEL_LIVE_BROWSER_TEST") != "1", reason="Opt-in public YouTube smoke test")
def test_youtube_search_over_authenticated_connect(tmp_path, monkeypatch):
    gateway = EgressGateway(audit_root=tmp_path).start()
    original_open = gateway.open_browser
    monkeypatch.setattr(gateway, "open_browser", lambda executable, url=None, **kwargs:
                        original_open(executable, url, headless=True, **kwargs))
    try:
        desktop = DesktopEnvironment(live_desktop=True, gateway=gateway)
        desktop._action_open_app({"app": "chrome"})
        desktop._action_youtube_search({"query": "hello world"})
        result = desktop.state["browser_navigation"]
        assert result["status"] == 200
        assert "youtube" in result["title"].lower()
        assert result["url"] == "https://www.youtube.com/results?search_query=hello+world"
        async def rendered_search():
            page = next(iter(gateway.browser.sessions.values()))[2]
            await page.locator("ytd-video-renderer").first.wait_for(state="visible", timeout=15000)
            return await page.locator("ytd-video-renderer").count()
        assert gateway.call(rendered_search(), timeout=20) > 0
    finally:
        gateway.close()
    assert any(r.get("method") == "CONNECT" and r.get("host") == "www.youtube.com"
               and r.get("decision") == "allow" for r in read_verified(gateway.audit.path))
