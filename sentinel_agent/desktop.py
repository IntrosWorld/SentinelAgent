from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from sentinel_agent.environment import CollegePortalEnvironment
from sentinel_agent.office_files import OfficeFileManager
from sentinel_agent.windows_control import WindowsController


class DesktopEnvironment(CollegePortalEnvironment):
    """Allowlisted Windows actions with a simulation mode for safe evaluation."""

    desktop_action_descriptions = {
        "open_app": "Open an allowlisted Windows application. Argument: app (chrome, edge, notepad, calculator).",
        "youtube_search": "Open YouTube search results in Chrome. Argument: query.",
        "inspect_app": "Read visible accessible controls from one application window. Arguments: app, optional title_contains.",
        "focus_app": "Bring one verified application window to the foreground. Arguments: app, optional title_contains.",
        "click_control": "Click one unambiguous accessible control. Arguments: app, target, optional title_contains and control_type.",
        "type_text": "Type text into one unambiguous editable control. Arguments: app, text, optional target and title_contains.",
        "press_key": "Press an allowlisted key in a verified application window. Arguments: app, key, optional title_contains.",
        "create_xlsx": "Create a styled Excel workbook. Arguments: filename, title, sheet_name, headers (list), rows (2D list).",
        "edit_xlsx": "Edit cells in an existing workspace Excel file with automatic backup. Arguments: filename, sheet_name, updates [{cell,value|formula,number_format?}].",
        "create_docx": "Create a styled Word document. Arguments: filename, title, paragraphs (list).",
        "append_docx": "Append paragraphs to an existing workspace Word document with automatic backup. Arguments: filename, paragraphs (list).",
        "create_pdf": "Create a styled PDF. Arguments: filename, title, paragraphs (list).",
        "append_pdf": "Append a new page to an existing workspace PDF with automatic backup. Arguments: filename, title, paragraphs (list).",
    }
    action_descriptions = {
        **CollegePortalEnvironment.action_descriptions,
        **desktop_action_descriptions,
    }

    app_commands = {
        "chrome": Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        "edge": Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        "notepad": Path(r"C:\Windows\System32\notepad.exe"),
        "calculator": Path(r"C:\Windows\System32\calc.exe"),
    }

    def __init__(self, disturbance: str | None = None, live_desktop: bool = False):
        super().__init__(disturbance)
        self.live_desktop = live_desktop
        self.state.update(
            {
                "desktop_mode": "live" if live_desktop else "simulated",
                "last_opened_app": None,
                "last_search": None,
                "last_url": None,
                "last_window_hint": None,
                "scratch_file": None,
                "active_window_title": None,
                "observed_controls": None,
                "last_control_action": None,
                "last_file_action": None,
            }
        )
        self._controller: WindowsController | None = None
        self._office: OfficeFileManager | None = None

    @property
    def controller(self) -> WindowsController:
        if self._controller is None:
            self._controller = WindowsController()
        return self._controller

    @property
    def office(self) -> OfficeFileManager:
        if self._office is None:
            self._office = OfficeFileManager()
        return self._office

    def _resolve_app(self, app: str) -> Path:
        normalized = app.strip().lower()
        path = self.app_commands.get(normalized)
        if path is None:
            raise RuntimeError(f"Application '{app}' is not on the allowlist")
        if self.live_desktop and not path.exists():
            raise RuntimeError(f"Application executable was not found: {path}")
        return path

    def _launch(self, command: list[str]) -> None:
        if self.live_desktop:
            subprocess.Popen(command)

    def _action_open_app(self, arguments: dict[str, Any]) -> str:
        app = str(arguments.get("app", "")).strip().lower()
        path = self._resolve_app(app)
        command = [str(path)]
        if app == "notepad" and self.live_desktop:
            artifacts = Path.cwd() / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            scratch = artifacts / f"sentinel_scratch_{uuid4().hex[:8]}.txt"
            scratch.touch(exist_ok=False)
            command.append(str(scratch))
            self.state["scratch_file"] = str(scratch)
            self.state["last_window_hint"] = scratch.name
        self._launch(command)
        self.state["last_opened_app"] = app
        mode = "Opened" if self.live_desktop else "Simulated opening"
        return f"{mode} {app}"

    def _window_hint(self, arguments: dict[str, Any]) -> str | None:
        explicit = arguments.get("title_contains")
        app = str(arguments.get("app", "")).strip().lower()
        if explicit:
            return str(explicit)
        if app == self.state.get("last_opened_app"):
            return self.state.get("last_window_hint")
        return None

    def _action_youtube_search(self, arguments: dict[str, Any]) -> str:
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise RuntimeError("YouTube search requires a non-empty query")
        chrome = self._resolve_app("chrome")
        url = "https://www.youtube.com/results?" + urlencode({"search_query": query})
        self._launch([str(chrome), url])
        self.state.update(
            {"last_opened_app": "chrome", "last_search": query, "last_url": url}
        )
        mode = "Opened" if self.live_desktop else "Simulated"
        return f"{mode} YouTube search for '{query}'"

    def _require_live(self, action: str) -> None:
        if not self.live_desktop:
            raise RuntimeError(f"{action} requires --live-desktop")

    def _action_inspect_app(self, arguments: dict[str, Any]) -> str:
        self._require_live("inspect_app")
        result = self.controller.inspect(
            str(arguments.get("app", "")), self._window_hint(arguments)
        )
        self.state["active_window_title"] = result["window_title"]
        self.state["observed_controls"] = result["controls"]
        return f"Observed {len(result['controls'])} controls in {result['window_title']}"

    def _action_focus_app(self, arguments: dict[str, Any]) -> str:
        self._require_live("focus_app")
        title = self.controller.focus(
            str(arguments.get("app", "")), self._window_hint(arguments)
        )
        self.state["active_window_title"] = title
        return f"Focused window '{title}'"

    def _action_click_control(self, arguments: dict[str, Any]) -> str:
        self._require_live("click_control")
        summary = self.controller.click(
            str(arguments.get("app", "")),
            str(arguments.get("target", "")),
            self._window_hint(arguments),
            arguments.get("control_type"),
        )
        self.state["last_control_action"] = {"action": "click", **summary}
        return f"Clicked {summary['type']} '{summary['name']}'"

    def _action_type_text(self, arguments: dict[str, Any]) -> str:
        self._require_live("type_text")
        text = str(arguments.get("text", ""))
        if not text:
            raise RuntimeError("type_text requires non-empty text")
        destination = self.controller.type_text(
            str(arguments.get("app", "")),
            text,
            arguments.get("target"),
            self._window_hint(arguments),
        )
        self.state["last_control_action"] = {
            "action": "type",
            "target": destination,
            "characters": len(text),
        }
        return f"Typed {len(text)} characters into '{destination}'"

    def _action_press_key(self, arguments: dict[str, Any]) -> str:
        self._require_live("press_key")
        key = self.controller.press_key(
            str(arguments.get("app", "")),
            str(arguments.get("key", "")),
            self._window_hint(arguments),
        )
        self.state["last_control_action"] = {"action": "key", "key": key}
        return f"Pressed {key}"

    def _record_file(self, action: str, result: dict[str, Any]) -> str:
        self.state["last_file_action"] = {"action": action, **result}
        return f"{action.replace('_', ' ').title()}: {result['output_path']}"

    def _action_create_xlsx(self, arguments: dict[str, Any]) -> str:
        result = self.office.create_xlsx(
            str(arguments.get("filename", "workbook.xlsx")),
            str(arguments.get("title", "SentinelAgent Workbook")),
            str(arguments.get("sheet_name", "Sheet1")),
            list(arguments.get("headers") or ["Item", "Value"]),
            list(arguments.get("rows") or [["Example", 1]]),
        )
        return self._record_file("create_xlsx", result)

    def _action_edit_xlsx(self, arguments: dict[str, Any]) -> str:
        result = self.office.edit_xlsx(
            str(arguments.get("filename", "")),
            str(arguments.get("sheet_name", "Sheet1")),
            list(arguments.get("updates") or []),
        )
        return self._record_file("edit_xlsx", result)

    def _action_create_docx(self, arguments: dict[str, Any]) -> str:
        result = self.office.create_docx(
            str(arguments.get("filename", "document.docx")),
            str(arguments.get("title", "SentinelAgent Document")),
            [str(item) for item in arguments.get("paragraphs") or ["Created by SentinelAgent."]],
        )
        return self._record_file("create_docx", result)

    def _action_append_docx(self, arguments: dict[str, Any]) -> str:
        result = self.office.append_docx(
            str(arguments.get("filename", "")),
            [str(item) for item in arguments.get("paragraphs") or []],
        )
        return self._record_file("append_docx", result)

    def _action_create_pdf(self, arguments: dict[str, Any]) -> str:
        result = self.office.create_pdf(
            str(arguments.get("filename", "document.pdf")),
            str(arguments.get("title", "SentinelAgent PDF")),
            [str(item) for item in arguments.get("paragraphs") or ["Created by SentinelAgent."]],
        )
        return self._record_file("create_pdf", result)

    def _action_append_pdf(self, arguments: dict[str, Any]) -> str:
        result = self.office.append_pdf(
            str(arguments.get("filename", "")),
            str(arguments.get("title", "Additional Content")),
            [str(item) for item in arguments.get("paragraphs") or []],
        )
        return self._record_file("append_pdf", result)
