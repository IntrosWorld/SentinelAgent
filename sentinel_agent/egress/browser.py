"""Run-owned Chromium sessions with in-memory proxy authentication."""

import asyncio
from contextlib import suppress

from sentinel_agent.egress.client import EgressBlocked, EgressError


class ManagedBrowser:
    def __init__(self, gateway):
        self.gateway = gateway
        self.runtime = None
        self.sessions = {}

    async def _route(self, route):
        # Also check subresources and navigations initiated by page scripts.
        decision = await self.gateway.policy.evaluate_url(route.request.url)
        if decision.allowed:
            await route.continue_()
        else:
            self.gateway.audit.write(kind="request", method="BROWSER",
                                     host=decision.host, port=decision.port,
                                     decision="block", rule=decision.rule)
            await route.abort("blockedbyclient")

    async def open(self, executable, url=None, *, headless=False, ready_selector=None):
        if url:
            decision = await self.gateway.policy.evaluate_url(url)
            if not decision.allowed:
                raise EgressBlocked(decision.rule)
        key = (str(executable), headless)
        phase = "startup"
        try:
            if self.runtime is None:
                from playwright.async_api import async_playwright
                self.runtime = await async_playwright().start()
            if key not in self.sessions:
                proxy = {"server": f"http://127.0.0.1:{self.gateway.proxy.port}",
                         "username": "sentinel", "password": self.gateway.proxy.token}
                browser = await self.runtime.chromium.launch(
                    executable_path=str(executable), headless=headless,
                    proxy=proxy, timeout=15000,
                    args=["--disable-quic", "--proxy-bypass-list=<-loopback>",
                          "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                )
                try:
                    context = await browser.new_context(service_workers="block", accept_downloads=False)
                    await context.route("**/*", self._route)
                    page = await context.new_page()
                except BaseException:
                    await browser.close()
                    raise
                self.sessions[key] = (browser, context, page)
            browser, context, page = self.sessions[key]
            if page.is_closed():
                page = await context.new_page()
                self.sessions[key] = (browser, context, page)
            if url:
                phase = "navigation"
                response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                if response is None or not 200 <= response.status < 400:
                    raise EgressError("Browser navigation returned no successful document")
                if ready_selector:
                    phase = "result verification"
                    await page.locator(ready_selector).first.wait_for(state="visible", timeout=15000)
            return {"url": page.url, "title": await page.title(),
                    "status": response.status if url else None}
        except ImportError:
            raise EgressError('Managed browsing requires: pip install -e ".[browser]"') from None
        except EgressError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Playwright error strings can contain URLs/query text. Do not expose them.
            raise EgressError(f"Managed browser {phase} failed ({type(exc).__name__})") from None

    async def close(self):
        try:
            for browser, context, page in self.sessions.values():
                with suppress(Exception):
                    await asyncio.wait_for(context.close(), 3)
                with suppress(Exception):
                    await asyncio.wait_for(browser.close(), 3)
        finally:
            self.sessions.clear()
            if self.runtime:
                try:
                    await asyncio.wait_for(self.runtime.stop(), 3)
                finally:
                    self.runtime = None
