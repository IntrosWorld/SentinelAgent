from __future__ import annotations

import time
from typing import Any


class WindowsControlError(RuntimeError):
    pass


class WindowsController:
    """Target-bound Windows UI Automation wrapper loaded only in live mode."""

    process_names = {
        "chrome": {"chrome.exe"},
        "edge": {"msedge.exe"},
        "notepad": {"notepad.exe"},
        "calculator": {"calculatorapp.exe", "calculator.exe", "applicationframehost.exe"},
    }
    key_map = {
        "ENTER": "{ENTER}",
        "TAB": "{TAB}",
        "ESC": "{ESC}",
        "SPACE": "{SPACE}",
        "CTRL+L": "^l",
        "CTRL+A": "^a",
    }

    def __init__(self) -> None:
        self._bound_windows: dict[str, Any] = {}

    def _libraries(self):
        try:
            import psutil
            from pywinauto import Desktop
            from pywinauto.keyboard import send_keys
        except ImportError as exc:
            raise WindowsControlError(
                "Desktop automation is not installed. Run: python -m pip install -e \".[desktop]\""
            ) from exc
        return Desktop, send_keys, psutil

    def _window(self, app: str, title_contains: str | None = None, timeout: float = 6.0):
        normalized = app.strip().lower()
        allowed_processes = self.process_names.get(normalized)
        if not allowed_processes:
            raise WindowsControlError(f"Application '{app}' is not controllable")
        if normalized == "calculator" and title_contains is None:
            # Packaged Calculator exposes its top-level window through
            # ApplicationFrameHost; require its exact product title family.
            title_contains = "Calculator"
        bound = self._bound_windows.get(normalized)
        if bound is not None:
            try:
                bound_title = bound.window_text().strip()
                if bound.is_visible() and (
                    not title_contains or title_contains.lower() in bound_title.lower()
                ):
                    return bound
            except RuntimeError:
                self._bound_windows.pop(normalized, None)
        Desktop, _, psutil = self._libraries()
        deadline = time.monotonic() + timeout
        matches: list[Any] = []
        while time.monotonic() < deadline:
            matches = []
            for window in Desktop(backend="uia").windows(visible_only=True):
                try:
                    process_name = psutil.Process(window.process_id()).name().lower()
                    title = window.window_text().strip()
                except (psutil.Error, RuntimeError):
                    continue
                if process_name not in allowed_processes or not title:
                    continue
                if title_contains and title_contains.lower() not in title.lower():
                    continue
                matches.append(window)
            if matches:
                break
            time.sleep(0.2)
        if not matches:
            hint = f" containing '{title_contains}'" if title_contains else ""
            raise WindowsControlError(f"No visible {app} window{hint} was found")
        if len(matches) != 1:
            titles = [window.window_text() for window in matches]
            raise WindowsControlError(
                f"Expected one {app} window but found {len(matches)}: {titles}. "
                "Provide title_contains to choose safely."
            )
        self._bound_windows[normalized] = matches[0]
        return matches[0]

    @staticmethod
    def _control_summary(control: Any) -> dict[str, str]:
        info = control.element_info
        return {
            "name": control.window_text().strip(),
            "type": str(getattr(info, "control_type", "")),
            "automation_id": str(getattr(info, "automation_id", "")),
        }

    def inspect(self, app: str, title_contains: str | None = None, limit: int = 80) -> dict[str, Any]:
        window = self._window(app, title_contains)
        controls: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for control in window.descendants():
            summary = self._control_summary(control)
            key = (summary["name"], summary["type"], summary["automation_id"])
            if not summary["name"] or key in seen:
                continue
            seen.add(key)
            controls.append(summary)
            if len(controls) >= min(max(limit, 1), 200):
                break
        return {"window_title": window.window_text(), "controls": controls}

    def focus(self, app: str, title_contains: str | None = None) -> str:
        window = self._window(app, title_contains)
        window.set_focus()
        return window.window_text()

    def _find_control(
        self,
        window: Any,
        target: str,
        control_type: str | None = None,
    ):
        target_lower = target.strip().lower()
        exact: list[Any] = []
        partial: list[Any] = []
        for control in window.descendants():
            try:
                name = control.window_text().strip()
                found_type = str(getattr(control.element_info, "control_type", ""))
                if not name or (control_type and found_type.lower() != control_type.lower()):
                    continue
                if name.lower() == target_lower:
                    exact.append(control)
                elif target_lower in name.lower():
                    partial.append(control)
            except RuntimeError:
                continue
        matches = exact or partial
        if len(matches) != 1:
            labels = [self._control_summary(control) for control in matches[:10]]
            raise WindowsControlError(
                f"Expected one control matching '{target}', found {len(matches)}: {labels}"
            )
        return matches[0]

    def click(
        self,
        app: str,
        target: str,
        title_contains: str | None = None,
        control_type: str | None = None,
    ) -> dict[str, str]:
        window = self._window(app, title_contains)
        window.set_focus()
        control = self._find_control(window, target, control_type)
        if not control.is_visible() or not control.is_enabled():
            raise WindowsControlError(f"Control '{target}' is not visible and enabled")
        summary = self._control_summary(control)
        control.click_input()
        return summary

    def type_text(
        self,
        app: str,
        text: str,
        target: str | None = None,
        title_contains: str | None = None,
    ) -> str:
        if len(text) > 2000:
            raise WindowsControlError("Text input exceeds the 2000-character safety limit")
        window = self._window(app, title_contains)
        window.set_focus()
        destination = None
        if target:
            destination = self._find_control(window, target)
        else:
            editable = [
                control
                for control in window.descendants()
                if str(getattr(control.element_info, "control_type", "")) in {"Edit", "Document"}
                and control.is_visible()
                and control.is_enabled()
            ]
            if len(editable) == 1:
                destination = editable[0]
            elif len(editable) > 1:
                names = [self._control_summary(control) for control in editable[:10]]
                raise WindowsControlError(
                    f"Multiple editable controls found; provide a target: {names}"
                )
        if destination is None:
            raise WindowsControlError("No unambiguous editable control was found")
        destination.click_input()
        try:
            destination.set_edit_text(text)
        except (AttributeError, NotImplementedError, RuntimeError):
            escaped = text
            for character in ("{", "}", "+", "^", "%", "~", "(", ")", "[", "]"):
                escaped = escaped.replace(character, "{" + character + "}")
            destination.type_keys(
                escaped,
                with_spaces=True,
                with_tabs=False,
                with_newlines=True,
                set_foreground=True,
                vk_packet=True,
            )
        return self._control_summary(destination)["name"] or "editable control"

    def press_key(
        self, app: str, key: str, title_contains: str | None = None
    ) -> str:
        normalized = key.strip().upper()
        sequence = self.key_map.get(normalized)
        if sequence is None:
            raise WindowsControlError(
                f"Key '{key}' is not allowed. Allowed keys: {sorted(self.key_map)}"
            )
        window = self._window(app, title_contains)
        window.set_focus()
        _, send_keys, _ = self._libraries()
        send_keys(sequence, pause=0.05)
        return normalized
