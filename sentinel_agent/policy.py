from sentinel_agent.schemas import Action, PolicyDecision, RiskLevel


class SafetyPolicy:
    """Deterministic policy boundary; it is deliberately not delegated to the LLM."""

    forbidden = {"enter_password", "disable_security", "bypass_captcha"}
    high_risk = {
        "send_email",
        "submit_form",
        "delete_file",
        "make_payment",
        "click_control",
        "type_text",
        "press_key",
        "edit_xlsx",
        "append_docx",
        "append_pdf",
    }
    medium_risk = {
        "download_report",
        "write_file",
        "open_app",
        "youtube_search",
        "focus_app",
        "inspect_app",
        "create_xlsx",
        "create_docx",
        "create_pdf",
    }

    def evaluate(self, action: Action) -> PolicyDecision:
        if action.name in self.forbidden:
            return PolicyDecision(
                risk=RiskLevel.FORBIDDEN,
                allowed=False,
                reason="This action is forbidden by the fixed safety policy.",
            )
        if action.name in self.high_risk:
            return PolicyDecision(
                risk=RiskLevel.HIGH,
                allowed=True,
                requires_approval=True,
                reason="This action changes external state and requires human approval.",
            )
        if action.name in self.medium_risk:
            return PolicyDecision(
                risk=RiskLevel.MEDIUM,
                allowed=True,
                reason="This action creates or downloads a local artifact.",
            )
        return PolicyDecision(
            risk=RiskLevel.LOW,
            allowed=True,
            reason="Read-only or local computation action.",
        )
