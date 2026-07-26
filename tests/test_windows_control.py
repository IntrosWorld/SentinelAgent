from unittest.mock import Mock

import pytest

from sentinel_agent.windows_control import WindowsControlError, WindowsController


def test_key_allowlist_rejects_dangerous_shortcut():
    controller = WindowsController()
    with pytest.raises(WindowsControlError, match="not allowed"):
        controller.press_key("notepad", "ALT+F4")


def test_control_summary_is_structured():
    control = Mock()
    control.window_text.return_value = "Search"
    control.element_info.control_type = "Edit"
    control.element_info.automation_id = "search-input"
    assert WindowsController._control_summary(control) == {
        "name": "Search",
        "type": "Edit",
        "automation_id": "search-input",
    }
