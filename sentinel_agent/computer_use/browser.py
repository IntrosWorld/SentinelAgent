"""Experimental, top-level DOM control through the authenticated browser.

Only fixed driver JavaScript executes: model actions contain opaque observation
IDs, never selectors or scripts. Click/fill are atomic guarded DOM operations,
not native pointer/keyboard events; pages requiring trusted events are outside
this prototype. Frames, shadow roots, downloads and credential entry are not
supported. Text/search/email/tel/URL inputs and textareas support fill; checkbox
controls support only generic click, and select menus are not exposed. Page text
and labels are untrusted input, not agent instructions.
"""

import asyncio
from contextlib import suppress
from secrets import token_urlsafe

from sentinel_agent.computer_use.schema import (
    BrowserAction, BrowserFailure, Element, Snapshot, StaleObservation,
)
from sentinel_agent.egress.browser import ManagedBrowser


# This object is retained as a JSHandle, not a page-global variable. A snapshot
# owns its actual DOM nodes and observer; replacing a node cannot preserve its ID.
_CAPTURE = """() => {
  const sensitive = (el) => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    let descriptor = [el.id, el.getAttribute('name'), el.getAttribute('autocomplete'),
      el.getAttribute('aria-label'), el.getAttribute('placeholder'),
      ...Array.from(el.labels || [], l => l.innerText)].join(' ');
    // Hidden bookkeeping fields must not disable an ordinary search form.
    // Recognized CSRF names are not credential-entry fields; other hidden
    // password/API-key/payment indicators still make the form sensitive.
    if (type === 'hidden')
      descriptor = descriptor.replace(/(?:csrf|xsrf|requestverification)[-_ ]?(?:middleware)?[-_ ]?token/gi, '');
    return ['password', 'file'].includes(type) ||
      /password|passwd|secret|token|api[-_ ]?key|credential|one-time|\\botp\\b|verification|cc-|credit|debit|card|payment|\\bcvv\\b|\\bcvc\\b|iban|routing|bank|account.?number|username/i.test(descriptor);
  };
  const visible = (el) => {
    const style = getComputedStyle(el), rect = el.getBoundingClientRect();
    const notTransparent = typeof el.checkVisibility !== 'function' ||
      el.checkVisibility({checkOpacity:true, checkVisibilityCSS:true});
    return el.isConnected && notTransparent && !el.closest('[hidden],[inert],[aria-hidden="true"]') &&
      style.display !== 'none' && style.visibility === 'visible' &&
      Number(style.opacity) !== 0 && rect.width > 0 && rect.height > 0;
  };
  const supportedURL = (url) => {
    try { return ['http:', 'https:'].includes(new URL(url, document.baseURI).protocol); }
    catch { return false; }
  };
  const supportedLink = (el) => !el.hasAttribute('href') || supportedURL(el.getAttribute('href'));
  const unsafe = (el) => {
    const form = el.form || el.closest('form');
    return (el.getAttribute('type') || '').toLowerCase() === 'hidden' || sensitive(el) || !supportedLink(el) ||
      !!(form && (!supportedURL(form.action) || Array.from(form.elements).some(sensitive))) ||
      !!(el.hasAttribute('formaction') && !supportedURL(el.formAction));
  };
  const describe = (el) => {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || 'text').toLowerCase();
    const editable = !unsafe(el) && !el.disabled && !el.readOnly &&
      (tag === 'textarea' || (tag === 'input' && ['text','search','email','tel','url'].includes(type)));
    const name = (el.getAttribute('aria-label') || Array.from(el.labels || [], l => l.innerText).join(' ') ||
      el.getAttribute('placeholder') || el.innerText || el.getAttribute('title') || type).trim().slice(0, 300);
    return {role: el.getAttribute('role') || (tag === 'a' ? 'link' : editable ? 'textbox' : tag),
      name, value: editable ? String(el.value || '').slice(0, 300) : '', editable};
  };
  const signature = (el) => JSON.stringify({
    description: describe(el), tag: el.tagName, type: el.getAttribute('type'),
    value: 'value' in el ? el.value : null, href: el.getAttribute('href'),
    disabled: !!el.disabled, readOnly: !!el.readOnly, checked: !!el.checked,
    formAction: el.form ? el.form.action : '', unsafe: unsafe(el),
    visible: visible(el), html: el.outerHTML,
  });
  const nodes = Array.from(document.querySelectorAll(
    'a[href],button,input,textarea,[role="button"],[role="link"],[role="textbox"]'
  )).slice(0, 1000).filter(el => visible(el) && !unsafe(el) && !el.disabled).slice(0, 60);
  const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
  let node, pieces = [], size = 0, count = 0;
  while ((node = walker.nextNode()) && count++ < 10000 && size < 12000) {
    const parent = node.parentElement;
    if (!parent || parent.closest('script,style,noscript,input,textarea,select,[contenteditable]') || !visible(parent)) continue;
    const value = node.textContent.trim();
    if (value) { pieces.push(value); size += value.length + 1; }
  }
  const state = {document, url: location.href, title: document.title, revision: 0,
    scrollX, scrollY, nodes, signatures: nodes.map(signature), signature, visible, unsafe, describe,
    text: pieces.join('\\n').slice(0,12000), descriptions: nodes.map(describe)};
  state.observer = new MutationObserver(() => state.revision++);
  state.observer.observe(document, {subtree:true, childList:true, attributes:true, characterData:true});
  return state;
}"""

_CHECK_AND_ACT = """(state, args) => {
  const stale = () => { state.revision++; return {status:'stale'}; };
  if (state.document !== document || state.url !== location.href || state.title !== document.title ||
      state.revision || state.observer.takeRecords().length || state.scrollX !== scrollX || state.scrollY !== scrollY)
    return stale();
  // Property writes (for example input.value) do not emit DOM mutations.
  if (state.nodes.some((node, index) => !node.isConnected || state.signature(node) !== state.signatures[index]))
    return stale();
  let el;
  if (args.index !== null) {
    el = state.nodes[args.index];
    if (!el || !el.isConnected || !state.visible(el) || state.unsafe(el) ||
        state.signature(el) !== state.signatures[args.index]) return stale();
    if (args.kind === 'fill' && !state.describe(el).editable) return {status:'blocked'};
  }
  if (!args.dispatch) return {status:'valid'};
  // No asynchronous gap between checking the observation and dispatching.
  if (args.kind === 'click') {
    el.click();
  } else if (args.kind === 'fill') {
    const prototype = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value').set;
    setter.call(el, args.text);
    el.dispatchEvent(new Event('input', {bubbles:true}));
    el.dispatchEvent(new Event('change', {bubbles:true}));
  } else if (args.kind === 'scroll') {
    window.scrollBy({top:(args.direction === 'up' ? -1 : 1) * Math.max(200, innerHeight * 0.75), behavior:'instant'});
  }
  return {status:'dispatched'};
}"""


class BrowserSession:
    """Caller-loop API; every Playwright operation runs on the gateway loop."""

    def __init__(self, gateway, executable, headless=True):
        self.gateway, self.executable, self.headless = gateway, executable, headless
        self._context = self._page = self._state = None
        self._snapshot = None
        self._indices = {}

    async def _submit(self, coroutine):
        try:
            return await asyncio.wrap_future(self.gateway.submit(coroutine))
        except (BrowserFailure, asyncio.CancelledError):
            raise
        except Exception as exc:
            raise BrowserFailure(f"Browser operation failed ({type(exc).__name__})") from None

    async def start(self, url):
        await self._submit(self._start(url))

    async def _start(self, url):
        if self._context is not None:
            raise BrowserFailure("Browser session has already started")
        if not self.gateway.enabled or self.gateway.proxy is None:
            raise BrowserFailure("Adaptive browsing requires an active egress gateway")
        decision = await self.gateway.policy.evaluate_url(url)
        if not decision.allowed:
            raise BrowserFailure(f"Browser destination blocked ({decision.rule})")
        if self.gateway.browser is None:
            self.gateway.browser = ManagedBrowser(self.gateway)
        manager = self.gateway.browser
        await manager.open(self.executable, headless=self.headless)
        browser = manager.sessions[(str(self.executable), self.headless)][0]
        # Reuse only the authenticated process, never another session's page,
        # cookies, context or observations. Gateway owns the process lifetime.
        self._context = await browser.new_context(service_workers="block", accept_downloads=False)
        try:
            await self._context.route("**/*", manager._route)
            self._page = await self._context.new_page()
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if response is None or not 200 <= response.status < 400:
                raise BrowserFailure("Browser navigation did not return a successful document")
        except BaseException:
            await self._close()
            raise

    async def _forget(self):
        if self._state is not None:
            with suppress(Exception):
                await self._state.evaluate("state => state.observer.disconnect()")
            with suppress(Exception):
                await self._state.dispose()
        self._state = self._snapshot = None
        self._indices = {}

    async def observe(self):
        return await self._submit(self._observe())

    async def _observe(self):
        if self._page is None or self._page.is_closed():
            raise BrowserFailure("Browser session is not open")
        await self._forget()
        self._state = await self._page.evaluate_handle(_CAPTURE)
        data = await self._state.evaluate("state => ({url:state.url,title:state.title,text:state.text,elements:state.descriptions})")
        elements = []
        for index, description in enumerate(data.pop("elements")):
            element_id = token_urlsafe(16)
            elements.append(Element(id=element_id, **description))
            self._indices[element_id] = index
        self._snapshot = Snapshot(id=token_urlsafe(24), elements=elements, **data)
        return self._snapshot

    def _check_id(self, action):
        if self._snapshot is None or action.snapshot_id != self._snapshot.id:
            raise StaleObservation("Observation has expired; observe again")
        if action.element_id is not None and action.element_id not in self._indices:
            raise StaleObservation("Element was not present in the current observation")

    async def validate(self, action: BrowserAction):
        await self._submit(self._apply(action, dispatch=False))

    async def act(self, action: BrowserAction):
        await self._submit(self._apply(action, dispatch=True))

    async def _apply(self, action, *, dispatch):
        self._check_id(action)
        if dispatch:
            # A destroyed document/handle found here is definitely pre-dispatch
            # and recoverable. Failures after the atomic dispatch remain terminal.
            await self._apply(action, dispatch=False)
        text = action.text
        if dispatch and action.kind == "fill":
            result = self.gateway.scanner.protect(text, "approved-site")
            self.gateway.audit.write(kind="request", method="BROWSER_FILL", decision="allow" if result.allowed else "block",
                                     rule="dlp", dlp=result.summary())
            if not result.allowed:
                raise BrowserFailure("Browser input blocked by DLP")
            text = result.body
        args = dict(kind=action.kind, index=self._indices.get(action.element_id), text=text,
                    direction=action.direction, dispatch=dispatch)
        try:
            result = await self._state.evaluate(_CHECK_AND_ACT, args)
        except Exception as exc:
            if not dispatch:
                raise StaleObservation("Page changed before action validation") from None
            # A navigation or timeout may occur after a side effect. Never retry
            # this ambiguous dispatch; a new run needs an explicit user choice.
            raise BrowserFailure(f"Browser action outcome is uncertain ({type(exc).__name__}); not retried") from None
        if result["status"] == "stale":
            raise StaleObservation("Page or target changed; observe again")
        if result["status"] == "blocked":
            raise BrowserFailure("Observed target does not permit this action")
        if dispatch:
            await self._forget()
            if action.kind == "wait":
                await asyncio.sleep(0.2)
            # Event handlers settle before the next observation. This never
            # waits for/repeats an action or treats navigation as task success.
            await asyncio.sleep(0)

    async def close(self):
        await self._submit(self._close())

    async def _close(self):
        await self._forget()
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = self._page = None
