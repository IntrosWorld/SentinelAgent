"""Experimental, app-independent Windows desktop backend.

The caller selects an existing window, not an executable or a shell command.
Accessibility identifies controls; optional window-only screenshots ground
pixel actions. Every dispatch rechecks the observation and consumes its IDs.
No generated scripts, app-specific workflows, or OS-wide network guarantees.

Native UIA calls run synchronously on the creating event-loop thread to retain
COM affinity. Deadlines cannot preempt a hung third-party UIA provider. Exact
image freshness is deliberately conservative: animations/caret blinking may
require another observation. Occluded, partly offscreen, elevated, modal,
separate tooltip/popup and credential windows are outside this first prototype.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
import re
from secrets import token_urlsafe
import sys
import threading
import time
from types import SimpleNamespace

from sentinel_agent.computer_use.desktop_schema import (
    ALLOWED_DESKTOP_KEYS, DesktopAction, DesktopElement, DesktopFailure,
    DesktopSnapshot, StaleDesktopObservation, validate_desktop_action,
)


_BLOCKED_APPS = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe",
    "bash.exe", "wsl.exe", "mintty.exe", "regedit.exe", "mmc.exe", "taskmgr.exe",
    "systemsettings.exe", "sechealthui.exe", "credentialuibroker.exe", "logonui.exe",
    "consent.exe", "keepass.exe", "keepassxc.exe", "bitwarden.exe", "1password.exe",
    "codex.exe", "chatgpt.exe", "sentinel desktop.exe",
})
_SENSITIVE_TITLE = re.compile(
    r"\b(?:codex|powershell|command prompt|windows terminal|password manager|"
    r"windows security|windows settings|user account control|credential|"
    r"sign[ -]?in|log[ -]?in|authentication|two.factor|verification code)\b", re.I,
)
_SENSITIVE_CONTROL = re.compile(
    r"\b(?:password|passcode|one.time code|verification code|api key|"
    r"secret key|credit card|card number|security code|terminal|powershell)\b", re.I,
)
_KEYS = {
    "ENTER": "{ENTER}", "TAB": "{TAB}", "SHIFT+TAB": "+{TAB}", "ESC": "{ESC}",
    "SPACE": "{SPACE}", "BACKSPACE": "{BACKSPACE}", "DELETE": "{DELETE}",
    "LEFT": "{LEFT}", "RIGHT": "{RIGHT}", "UP": "{UP}", "DOWN": "{DOWN}",
    "HOME": "{HOME}", "END": "{END}", "PAGEUP": "{PGUP}", "PAGEDOWN": "{PGDN}",
    "CTRL+A": "^a", "CTRL+Z": "^z", "CTRL+Y": "^y",
}
assert set(_KEYS) == set(ALLOWED_DESKTOP_KEYS)


def _blocked(app: str, title: str) -> bool:
    return app.casefold() in _BLOCKED_APPS or bool(_SENSITIVE_TITLE.search(title))


def _rect(rectangle):
    return tuple(int(getattr(rectangle, field)) for field in ("left", "top", "right", "bottom"))


class _WindowsApi:
    """Small native geometry/focus/input guard, lazy and mockable in tests."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.user32 = user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        for name, args, result in (
            ("GetForegroundWindow", [], wintypes.HWND),
            ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("GetLastActivePopup", [wintypes.HWND], wintypes.HWND),
            ("WindowFromPoint", [wintypes.POINT], wintypes.HWND),
            ("GetWindow", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("GetDpiForWindow", [wintypes.HWND], wintypes.UINT),
            ("SetThreadDpiAwarenessContext", [ctypes.c_void_p], ctypes.c_void_p),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
        ):
            function = getattr(user32, name)
            function.argtypes, function.restype = args, result
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL

    def is_elevated(self, pid):
        """Fail closed if either the process or TokenElevation cannot be read."""
        process = self.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not process:
            raise DesktopFailure("Selected process privileges could not be established")
        token = self.wintypes.HANDLE()
        try:
            if not self.advapi32.OpenProcessToken(process, 0x0008, self.ctypes.byref(token)):  # TOKEN_QUERY
                raise DesktopFailure("Selected process privileges could not be established")
            elevation, returned = self.wintypes.DWORD(), self.wintypes.DWORD()
            if not self.advapi32.GetTokenInformation(token, 20, self.ctypes.byref(elevation),
                                                     self.ctypes.sizeof(elevation), self.ctypes.byref(returned)):
                raise DesktopFailure("Selected process privileges could not be established")
            if returned.value != self.ctypes.sizeof(elevation):
                raise DesktopFailure("Selected process privileges could not be established")
            return bool(elevation.value)
        finally:
            if token.value:
                self.kernel32.CloseHandle(token)
            self.kernel32.CloseHandle(process)

    @contextmanager
    def pixels(self):
        previous = self.user32.SetThreadDpiAwarenessContext(self.ctypes.c_void_p(-4))
        if not previous:
            raise DesktopFailure("Physical-pixel DPI context is unavailable")
        try:
            yield
        finally:
            self.user32.SetThreadDpiAwarenessContext(previous)

    def rect(self, handle):
        result = self.wintypes.RECT()
        if not self.user32.GetWindowRect(handle, self.ctypes.byref(result)):
            raise DesktopFailure("Selected window geometry is unavailable")
        return _rect(result)

    def dpi(self, handle):
        value = self.user32.GetDpiForWindow(handle)
        if not value:
            raise DesktopFailure("Selected window DPI is unavailable")
        return value

    def screen(self):
        x, y, width, height = [self.user32.GetSystemMetrics(n) for n in (76, 77, 78, 79)]
        return x, y, x + width, y + height

    def foreground(self, handle):
        return int(self.user32.GetForegroundWindow() or 0) == handle

    def popup(self, handle):
        return int(self.user32.GetLastActivePopup(handle) or 0) != handle

    def point_window(self, x, y):
        hit = self.user32.WindowFromPoint(self.wintypes.POINT(x, y))
        return int(self.user32.GetAncestor(hit, 2) or 0)

    def modifiers_released(self):
        return not any(self.user32.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C))

    def uncovered(self, handle, bounds):
        # Conservatively reject every visible higher-Z-order intersecting window,
        # including a transparent overlay: sampled points alone miss occlusion.
        above = self.user32.GetWindow(handle, 3)  # GW_HWNDPREV
        count = 0
        while above:
            count += 1
            if count > 1000:
                return False
            if self.user32.IsWindowVisible(above):
                other = self.rect(above)
                if max(bounds[0], other[0]) < min(bounds[2], other[2]) and max(bounds[1], other[1]) < min(bounds[3], other[3]):
                    return False
            above = self.user32.GetWindow(above, 3)
        return True

    def type_unicode(self, text):
        """Literal UTF-16 SendInput packets, never pywinauto's key grammar."""
        ctypes, wintypes = self.ctypes, self.wintypes

        class KeyboardInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class HardwareInput(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class Payload(ctypes.Union):
            _fields_ = [("ki", KeyboardInput), ("mi", MouseInput), ("hi", HardwareInput)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

        data = text.encode("utf-16-le")
        units = [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
        inputs = (Input * (len(units) * 2))()
        for index, unit in enumerate(units):
            for offset, flags in enumerate((4, 6)):  # UNICODE, then UNICODE|KEYUP
                inputs[index * 2 + offset].type = 1
                inputs[index * 2 + offset].payload.ki = KeyboardInput(0, unit, flags, 0, 0)
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        if inputs and self.user32.SendInput(len(inputs), inputs, ctypes.sizeof(Input)) != len(inputs):
            raise DesktopFailure("Text input was not fully dispatched; do not retry automatically")


class WindowsDesktopSession:
    """One existing window for one run. Closing the session never closes the app."""

    def __init__(self, window_id: int, capture_images: bool = True):
        if type(window_id) is not int or window_id <= 0:
            raise ValueError("Select a positive native window ID")
        self.window_id, self.capture_images = window_id, bool(capture_images)
        self._deps = None
        self._thread = None
        self._identity = None
        self._snapshot = None
        self._signature = None
        self._targets = {}
        self._bounds = None
        self._closed = False

    def _libraries(self):
        if self._deps is None:
            if sys.platform != "win32":
                raise DesktopFailure("Desktop control requires Windows")
            try:
                import psutil
                from pywinauto import Desktop, mouse, keyboard
                from PIL import ImageGrab
                self._deps = SimpleNamespace(Desktop=Desktop, psutil=psutil, mouse=mouse,
                                             keyboard=keyboard, grab=ImageGrab.grab, api=_WindowsApi())
            except (ImportError, AttributeError, OSError):
                raise DesktopFailure('Install Windows desktop dependencies: pip install -e ".[desktop]"') from None
        return self._deps

    @staticmethod
    def list_windows():
        session = WindowsDesktopSession(1, capture_images=False)
        dependencies = session._libraries()
        results = []
        with dependencies.api.pixels():
            for window in dependencies.Desktop(backend="uia").windows(visible_only=True):
                try:
                    app = dependencies.psutil.Process(window.process_id()).name()
                    title = window.window_text().strip()
                    if (title and not _blocked(app, title) and window.is_enabled()
                            and str(window.process_id()) != os.environ.get("SENTINEL_DESKTOP_HOST_PID")):
                        results.append({"window_id": int(window.handle), "app": app, "title": title})
                except Exception:
                    continue
        return results

    def _assert_thread(self):
        if self._thread != threading.get_ident():
            raise DesktopFailure("Desktop session must remain on its original thread")
        if self._closed or self._identity is None:
            raise DesktopFailure("Desktop session is not open")

    def _window(self, *, allow_focus=False):
        dependencies = self._libraries()
        window = dependencies.Desktop(backend="uia").window(handle=self.window_id).wrapper_object()
        if not window.is_visible() or not window.is_enabled():
            raise DesktopFailure("Selected window is hidden, disabled, or has a modal dialog")
        process = dependencies.psutil.Process(window.process_id())
        if str(window.process_id()) == os.environ.get("SENTINEL_DESKTOP_HOST_PID"):
            raise DesktopFailure("The controller cannot operate its own approval window")
        identity = (window.process_id(), process.create_time(), process.name().casefold(),
                    tuple(window.element_info.element.GetRuntimeId()))
        if self._identity is not None and identity != self._identity:
            raise DesktopFailure("Selected window process changed")
        title = window.window_text()
        if _blocked(identity[2], title):
            raise DesktopFailure("Selected application or window is outside the permitted desktop scope")
        api = dependencies.api
        if api.is_elevated(identity[0]) or api.is_elevated(os.getpid()):
            raise DesktopFailure("Elevated applications or an elevated controller are not supported")
        if api.popup(self.window_id):
            raise DesktopFailure("A modal or popup window requires a new user selection")
        if not api.foreground(self.window_id):
            if not allow_focus:
                raise DesktopFailure("Selected window lost focus; input stopped")
            window.set_focus()
            if not api.foreground(self.window_id):
                raise DesktopFailure("Selected window could not receive focus")
        if not api.modifiers_released():
            raise DesktopFailure("Release keyboard modifiers before desktop control")
        bounds = _rect(window.rectangle())
        if api.rect(self.window_id) != bounds:
            raise DesktopFailure("UIA and physical window coordinates disagree")
        left, top, right, bottom = bounds
        screen = api.screen()
        if not (screen[0] <= left < right <= screen[2] and screen[1] <= top < bottom <= screen[3]):
            raise DesktopFailure("Selected window must be entirely visible on the desktop")
        if (right - left) * (bottom - top) > 20_000_000:
            raise DesktopFailure("Selected window exceeds the capture size limit")
        if not api.uncovered(self.window_id, bounds):
            raise DesktopFailure("Selected window is covered by another window")
        return window, identity, title, bounds, api.dpi(self.window_id)

    @staticmethod
    def _control_content(control, role):
        value, editable = "", False
        if role in {"Edit", "Document"}:
            try:
                pattern = control.iface_value
                editable = not bool(pattern.CurrentIsReadOnly)
                value = str(pattern.CurrentValue)
            except Exception:
                try:
                    document = control.iface_text.DocumentRange
                    value = str(document.GetText(4000))
                    # UIA_IsReadOnlyAttributeId. Unknown/mixed is not permission.
                    readonly = document.GetAttributeValue(40015)
                    editable = type(readonly) is bool and not readonly
                except Exception:
                    pass
        return value[:4000], editable

    def _read_state(self):
        window, identity, title, bounds, dpi = self._window()
        controls = window.descendants()
        if len(controls) > 2000:
            raise DesktopFailure("Accessibility tree exceeds the inspection limit")
        records, contents = [], []
        for control in controls:
            if not control.is_visible():
                continue
            info = control.element_info
            # Check sensitivity before asking for any text value or screenshot.
            if bool(info.element.CurrentIsPassword):
                raise DesktopFailure("Password fields are outside the permitted desktop scope")
            role = str(info.control_type)
            name = str(info.name or "")
            if _SENSITIVE_CONTROL.search(name):
                raise DesktopFailure("Sensitive or terminal controls are outside the permitted desktop scope")
            absolute = _rect(control.rectangle())
            left, top = max(absolute[0], bounds[0]), max(absolute[1], bounds[1])
            right, bottom = min(absolute[2], bounds[2]), min(absolute[3], bounds[3])
            if left >= right or top >= bottom:
                continue
            value, editable = self._control_content(control, role)
            enabled = bool(control.is_enabled())
            relative = left - bounds[0], top - bounds[1], right - bounds[0], bottom - bounds[1]
            runtime_id = tuple(info.element.GetRuntimeId())
            metadata = {"role": role, "name": name[:300], "bounds": relative,
                        "enabled": enabled, "editable": editable and enabled, "password": False}
            fingerprint = {**metadata, "runtime_id": runtime_id, "value": value,
                           "automation_id": str(info.automation_id or ""),
                           "keyboard_focus": bool(control.has_keyboard_focus())}
            records.append((metadata, fingerprint, control))
            if value:
                contents.append(value)
        image, digest = None, None
        if self.capture_images:
            capture = self._libraries().grab(bbox=bounds, all_screens=True)
            if capture.size != (bounds[2] - bounds[0], bounds[3] - bounds[1]):
                raise DesktopFailure("Screenshot dimensions do not match physical window pixels")
            with io.BytesIO() as output:
                capture.convert("RGB").save(output, format="PNG")
                data = output.getvalue()
            image = base64.b64encode(data).decode("ascii")
            digest = hashlib.sha256(data).hexdigest()
        # A user focus/geometry change during observation invalidates the image.
        _, last_identity, last_title, last_bounds, last_dpi = self._window()
        if (identity, title, bounds, dpi) != (last_identity, last_title, last_bounds, last_dpi):
            raise StaleDesktopObservation("Window changed during observation")
        signature = hashlib.sha256(json.dumps(
            [identity, title, bounds, dpi, [record[1] for record in records], digest],
            sort_keys=True, ensure_ascii=True,
        ).encode()).hexdigest()
        return identity, title, bounds, records, "\n".join(contents)[:12000], image, signature

    async def start(self):
        if self._identity is not None or self._closed:
            raise DesktopFailure("Desktop session cannot be restarted")
        self._thread = threading.get_ident()
        try:
            with self._libraries().api.pixels():
                _, identity, _, _, _ = self._window(allow_focus=True)
                self._identity = identity
        except DesktopFailure:
            raise
        except Exception:
            raise DesktopFailure("Selected desktop window is unavailable") from None

    async def focus(self):
        """Explicit reacquisition after the user approves in their terminal.

        This must not be called speculatively by the action loop. Validation
        still checks the old observation afterward; activation is not approval
        of changed content. The user should leave the target untouched while
        an approved input is executing.
        """
        self._assert_thread()
        try:
            with self._libraries().api.pixels():
                self._window(allow_focus=True)
        except DesktopFailure:
            raise
        except Exception:
            raise DesktopFailure("Selected desktop window could not be reactivated") from None

    async def observe(self):
        self._assert_thread()
        self._forget()
        try:
            with self._libraries().api.pixels():
                identity, title, bounds, records, content, image, signature = self._read_state()
                elements = []
                for metadata, _, control in records[:250]:
                    element_id = token_urlsafe(18)
                    elements.append(DesktopElement(id=element_id, **metadata))
                    self._targets[element_id] = control
                self._snapshot = DesktopSnapshot(
                    id=token_urlsafe(24), window_id=str(self.window_id), app=identity[2], title=title,
                    width=bounds[2] - bounds[0], height=bounds[3] - bounds[1],
                    elements=elements, text=content, screenshot=image,
                )
                self._bounds, self._signature = bounds, signature
                return self._snapshot
        except DesktopFailure:
            self._forget()
            raise
        except Exception:
            self._forget()
            raise DesktopFailure("Desktop observation is unavailable") from None

    def _checked(self, action):
        self._assert_thread()
        if self._snapshot is None or action.snapshot_id != self._snapshot.id:
            raise StaleDesktopObservation("Desktop observation expired; observe again")
        try:
            validate_desktop_action(action, self._snapshot)
        except ValueError:
            raise DesktopFailure("Desktop action has an invalid or unobserved target") from None
        if action.x is not None and not self._snapshot.screenshot:
            raise DesktopFailure("Coordinate actions require a screenshot observation")
        if self._read_state()[-1] != self._signature:
            self._forget()
            raise StaleDesktopObservation("Desktop changed before input; observe again")
        if action.element_id:
            target = next(element for element in self._snapshot.elements if element.id == action.element_id)
            start = ((target.bounds[0] + target.bounds[2]) // 2, (target.bounds[1] + target.bounds[3]) // 2)
        elif action.x is not None:
            start = action.x, action.y
        else:
            start = None
        points = []
        if start is not None:
            points.append(start)
        if action.kind == "drag":
            points.extend((round(action.x + (action.end_x - action.x) * i / 20),
                           round(action.y + (action.end_y - action.y) * i / 20)) for i in range(1, 21))
        absolute = [(x + self._bounds[0], y + self._bounds[1]) for x, y in points]
        if any(self._libraries().api.point_window(x, y) != self.window_id for x, y in absolute):
            raise DesktopFailure("Input target is covered by another window")
        return absolute

    async def validate(self, action: DesktopAction):
        try:
            with self._libraries().api.pixels():
                self._checked(action)
        except DesktopFailure:
            raise
        except Exception:
            raise DesktopFailure("Desktop action could not be validated") from None

    async def act(self, action: DesktopAction):
        try:
            dependencies = self._libraries()
            with dependencies.api.pixels():
                points = self._checked(action)
                target = self._targets.get(action.element_id)
                # Consume before dispatch, even if an input function raises.
                self._forget()
                if action.kind == "finish":
                    raise DesktopFailure("Only independent verification can finish a desktop task")
                if action.kind == "wait":
                    pass
                elif action.kind == "click":
                    dependencies.mouse.click(coords=points[0])
                elif action.kind == "double_click":
                    dependencies.mouse.double_click(coords=points[0])
                elif action.kind == "hover":
                    dependencies.mouse.move(coords=points[0])
                elif action.kind == "scroll":
                    dependencies.mouse.scroll(coords=points[0], wheel_dist=3 if action.direction == "up" else -3)
                elif action.kind == "drag":
                    try:
                        dependencies.mouse.press(coords=points[0])
                        for point in points[1:]:
                            self._window()
                            if dependencies.api.point_window(*point) != self.window_id:
                                raise DesktopFailure("Drawing target became covered; input stopped")
                            dependencies.mouse.move(coords=point)
                            time.sleep(0.01)
                    finally:
                        dependencies.mouse.release(button="left")
                elif action.kind == "type":
                    dependencies.mouse.click(coords=points[0])
                    self._window()
                    if not target.has_keyboard_focus():
                        raise DesktopFailure("Observed editable target did not receive keyboard focus")
                    dependencies.api.type_unicode(action.text)
                elif action.kind == "press_key":
                    dependencies.keyboard.send_keys(_KEYS[action.key], pause=0.02)
            if action.kind == "wait":
                await asyncio.sleep(0.2)
        except DesktopFailure:
            raise
        except Exception:
            raise DesktopFailure("Desktop input outcome is uncertain; do not retry automatically") from None

    def _forget(self):
        self._snapshot = self._signature = self._bounds = None
        self._targets = {}

    async def close(self):
        self._forget()
        self._identity = None
        self._closed = True
        self._deps = None
