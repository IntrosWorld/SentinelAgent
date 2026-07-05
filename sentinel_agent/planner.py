import re
from pathlib import Path

from sentinel_agent.desktop import DesktopEnvironment
from sentinel_agent.llm import ModelUnavailable, PlannerModel
from sentinel_agent.schemas import Action, Plan


class HybridPlanner:
    """Prefer a local model, but preserve a deterministic offline demo path."""

    def __init__(self, model: PlannerModel):
        self.model = model

    async def plan(
        self,
        instruction: str,
        state: dict,
        available_actions: dict[str, str],
        use_model: bool = True,
    ) -> tuple[Plan, str]:
        if use_model:
            try:
                plan = await self.model.create_plan(
                    instruction, available_actions, state
                )
                self._validate_actions(plan, available_actions)
                return plan, self.model.source
            except (ModelUnavailable, ValueError):
                pass
        return self._fallback_plan(instruction), "deterministic-fallback"

    @staticmethod
    def _validate_actions(plan: Plan, allowed: dict[str, str]) -> None:
        invalid = [action.name for action in plan.actions if action.name not in allowed]
        if invalid:
            raise ValueError(f"Model invented unavailable actions: {invalid}")
        if len(plan.actions) > 10:
            raise ValueError("Plan exceeded the maximum step budget")

    @staticmethod
    def _fallback_plan(instruction: str) -> Plan:
        lower = instruction.lower()
        office_match = re.search(r"([\w.-]+\.(xlsx|docx|pdf))", instruction, re.IGNORECASE)
        if office_match:
            filename = office_match.group(1)
            extension = office_match.group(2).lower()
            content_match = re.search(r"\bwith\s+content\s+(.+)$", instruction, re.IGNORECASE)
            content = content_match.group(1).strip(" \"'") if content_match else "Created by SentinelAgent."
            is_edit = bool(re.search(r"\b(edit|append|add|update|set)\b", lower))
            if extension == "xlsx":
                if is_edit:
                    cell_match = re.search(
                        r"\bset\s+([A-Z]+\d+)\s+to\s+(.+?)\s+(?:in|on)\b",
                        instruction,
                        re.IGNORECASE,
                    )
                    cell = cell_match.group(1).upper() if cell_match else "A1"
                    value: object = cell_match.group(2).strip(" \"'") if cell_match else content
                    try:
                        value = float(value) if "." in str(value) else int(value)
                    except ValueError:
                        pass
                    return Plan(
                        goal=instruction,
                        actions=[Action(name="edit_xlsx", arguments={"filename": filename, "sheet_name": "Sheet1", "updates": [{"cell": cell, "value": value}]}, expected_result="Workbook is updated and backed up")],
                    )
                return Plan(
                    goal=instruction,
                    actions=[Action(name="create_xlsx", arguments={"filename": filename, "title": Path(filename).stem.replace("_", " ").title(), "sheet_name": "Sheet1", "headers": ["Item", "Value"], "rows": [[content, 1]]}, expected_result="Styled workbook is created and rendered")],
                )
            if extension == "docx":
                action = "append_docx" if is_edit else "create_docx"
                args = {"filename": filename, "paragraphs": [content]}
                if action == "create_docx":
                    args["title"] = Path(filename).stem.replace("_", " ").title()
                return Plan(goal=instruction, actions=[Action(name=action, arguments=args, expected_result="Word document is written and verified")])
            action = "append_pdf" if is_edit else "create_pdf"
            args = {"filename": filename, "paragraphs": [content], "title": Path(filename).stem.replace("_", " ").title()}
            return Plan(goal=instruction, actions=[Action(name=action, arguments=args, expected_result="PDF is written and rendered")])

        if any(word in lower for word in ("chrome", "youtube", "notepad", "calculator", "edge")):
            actions: list[Action] = []
            app_match = next(
                (app for app in ("chrome", "edge", "notepad", "calculator") if app in lower),
                None,
            )
            if app_match:
                actions.append(
                    Action(
                        name="open_app",
                        arguments={"app": app_match},
                        expected_result=f"{app_match.title()} is opened",
                    )
                )
            if "youtube" in lower:
                query_match = re.search(r"\bsearch(?:\s+for)?\s+(.+?)\s*$", instruction, re.IGNORECASE)
                query = query_match.group(1).strip(" \"'") if query_match else "hello world"
                actions.append(
                    Action(
                        name="youtube_search",
                        arguments={"query": query},
                        expected_result=f"YouTube search results for {query} are open",
                    )
                )
            type_match = re.search(
                r"\btype\s+(.+?)\s*$", instruction, re.IGNORECASE
            )
            if type_match and app_match:
                text = type_match.group(1).strip(" \"'")
                actions.extend(
                    [
                        Action(
                            name="focus_app",
                            arguments={"app": app_match},
                            expected_result=f"{app_match.title()} window is focused",
                        ),
                        Action(
                            name="type_text",
                            arguments={"app": app_match, "text": text},
                            expected_result=f"Text is entered into {app_match}",
                        ),
                    ]
                )
            click_match = re.search(
                r"\bclick\s+(?:the\s+)?(.+?)(?:\s+button)?\s*$",
                instruction,
                re.IGNORECASE,
            )
            if click_match and app_match:
                target = click_match.group(1).strip(" \"'")
                actions.extend(
                    [
                        Action(
                            name="inspect_app",
                            arguments={"app": app_match},
                            expected_result=f"Accessible {app_match} controls are observed",
                        ),
                        Action(
                            name="click_control",
                            arguments={"app": app_match, "target": target},
                            expected_result=f"Control '{target}' is clicked",
                        ),
                    ]
                )
            key_match = re.search(
                r"\bpress\s+(enter|tab|escape|esc|space|ctrl\+l|ctrl\+a)\s*$",
                instruction,
                re.IGNORECASE,
            )
            if key_match and app_match:
                actions.append(
                    Action(
                        name="press_key",
                        arguments={"app": app_match, "key": key_match.group(1).upper()},
                        expected_result=f"Key {key_match.group(1).upper()} is pressed",
                    )
                )
            if re.search(r"\b(inspect|read)\b", lower) and app_match and not click_match:
                actions.append(
                    Action(
                        name="inspect_app",
                        arguments={"app": app_match},
                        expected_result=f"Accessible {app_match} controls are observed",
                    )
                )
            return Plan(goal=instruction, actions=actions)

        actions = [
            Action(name="open_portal", expected_result="Portal is open"),
            Action(name="read_attendance", expected_result="Attendance data is available"),
            Action(name="read_timetable", expected_result="Timetable data is available"),
            Action(
                name="calculate_shortage",
                arguments={"threshold": 75},
                expected_result="Subjects below 75% are identified",
            ),
            Action(name="create_report", expected_result="A shortage report exists"),
        ]
        if "download" in lower:
            actions.append(Action(name="download_report", expected_result="Report is downloaded"))
        if "email" in lower or "send" in lower:
            actions.append(Action(name="send_email", expected_result="Email is sent"))
        return Plan(goal=instruction, actions=actions)
