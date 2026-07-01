from __future__ import annotations

from typing import Any, Callable

from sentinel_agent.schemas import Action, Observation


class CollegePortalEnvironment:
    """A reproducible demo environment used for development and benchmarking."""

    action_descriptions = {
        "open_portal": "Open the simulated student portal.",
        "read_attendance": "Read subject attendance percentages from the open portal.",
        "read_timetable": "Read required weekly classes for each subject.",
        "calculate_shortage": "Identify subjects below the requested attendance threshold (default 75).",
        "create_report": "Create a local report from calculated attendance shortages.",
        "download_report": "Download the generated report locally.",
        "send_email": "Send the report by email; always requires human approval.",
    }

    def __init__(self, disturbance: str | None = None):
        self.state: dict[str, Any] = {
            "page": "home",
            "attendance": None,
            "timetable": None,
            "shortages": None,
            "report": None,
            "downloaded": False,
            "email_sent": False,
            "disturbance": disturbance,
            "popup_visible": disturbance == "popup",
        }

    def public_state(self) -> dict[str, Any]:
        return dict(self.state)

    def execute(self, action: Action) -> Observation:
        handler: Callable[[dict[str, Any]], str] | None = getattr(
            self, f"_action_{action.name}", None
        )
        if handler is None:
            return Observation(action=action, success=False, message="Unknown action", state=self.public_state())
        try:
            message = handler(action.arguments)
            return Observation(action=action, success=True, message=message, state=self.public_state())
        except RuntimeError as exc:
            return Observation(action=action, success=False, message=str(exc), state=self.public_state())

    def _require_portal(self) -> None:
        if self.state["page"] != "portal":
            raise RuntimeError("Portal is not open")

    def _action_open_portal(self, _: dict[str, Any]) -> str:
        self.state["page"] = "portal"
        if self.state["popup_visible"]:
            self.state["popup_visible"] = False
            return "Portal opened; an announcement popup was detected and safely dismissed"
        return "Portal opened"

    def _action_read_attendance(self, _: dict[str, Any]) -> str:
        self._require_portal()
        self.state["attendance"] = {"AIML": 68, "DBMS": 82, "OS": 73, "Networks": 88}
        return "Attendance table extracted"

    def _action_read_timetable(self, _: dict[str, Any]) -> str:
        self._require_portal()
        self.state["timetable"] = {"AIML": 4, "DBMS": 3, "OS": 4, "Networks": 3}
        return "Weekly timetable extracted"

    def _action_calculate_shortage(self, arguments: dict[str, Any]) -> str:
        if self.state["attendance"] is None:
            raise RuntimeError("Attendance must be read before calculating shortages")
        threshold = float(arguments.get("threshold", 75))
        self.state["shortages"] = {
            subject: value
            for subject, value in self.state["attendance"].items()
            if value < threshold
        }
        return f"Found {len(self.state['shortages'])} subjects below {threshold:g}%"

    def _action_create_report(self, _: dict[str, Any]) -> str:
        if self.state["shortages"] is None:
            raise RuntimeError("Shortages must be calculated before creating a report")
        lines = ["Attendance Shortage Report", ""]
        lines.extend(f"- {name}: {value}%" for name, value in self.state["shortages"].items())
        self.state["report"] = "\n".join(lines)
        return "Report created"

    def _action_download_report(self, _: dict[str, Any]) -> str:
        if not self.state["report"]:
            raise RuntimeError("Report must be created before download")
        self.state["downloaded"] = True
        return "Report downloaded in the simulated environment"

    def _action_send_email(self, _: dict[str, Any]) -> str:
        if not self.state["report"]:
            raise RuntimeError("Report must be created before email")
        self.state["email_sent"] = True
        return "Simulated email sent"

