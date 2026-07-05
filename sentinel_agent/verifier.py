from sentinel_agent.schemas import Observation


class StateVerifier:
    """Checks observable postconditions instead of asking the planning model to self-grade."""

    postconditions = {
        "open_portal": lambda s: s.get("page") == "portal",
        "read_attendance": lambda s: isinstance(s.get("attendance"), dict),
        "read_timetable": lambda s: isinstance(s.get("timetable"), dict),
        "calculate_shortage": lambda s: isinstance(s.get("shortages"), dict),
        "create_report": lambda s: bool(s.get("report")),
        "download_report": lambda s: s.get("downloaded") is True,
        "send_email": lambda s: s.get("email_sent") is True,
        "open_app": lambda s: bool(s.get("last_opened_app")),
        "youtube_search": lambda s: bool(s.get("last_search")) and "youtube.com/results" in str(s.get("last_url")),
        "inspect_app": lambda s: isinstance(s.get("observed_controls"), list),
        "focus_app": lambda s: bool(s.get("active_window_title")),
        "click_control": lambda s: (s.get("last_control_action") or {}).get("action") == "click",
        "type_text": lambda s: (s.get("last_control_action") or {}).get("action") == "type",
        "press_key": lambda s: (s.get("last_control_action") or {}).get("action") == "key",
        "create_xlsx": lambda s: (s.get("last_file_action") or {}).get("action") == "create_xlsx",
        "edit_xlsx": lambda s: (s.get("last_file_action") or {}).get("action") == "edit_xlsx" and bool((s.get("last_file_action") or {}).get("backup_path")),
        "create_docx": lambda s: (s.get("last_file_action") or {}).get("action") == "create_docx",
        "append_docx": lambda s: (s.get("last_file_action") or {}).get("action") == "append_docx" and bool((s.get("last_file_action") or {}).get("backup_path")),
        "create_pdf": lambda s: (s.get("last_file_action") or {}).get("action") == "create_pdf",
        "append_pdf": lambda s: (s.get("last_file_action") or {}).get("action") == "append_pdf" and bool((s.get("last_file_action") or {}).get("backup_path")),
    }

    def verify(self, observation: Observation) -> tuple[bool, str]:
        if not observation.success:
            return False, observation.message
        check = self.postconditions.get(observation.action.name)
        if check is None:
            return False, "No verifier is registered for this action"
        if not check(observation.state):
            return False, f"Expected state was not observed after {observation.action.name}"
        return True, "Postcondition verified"
