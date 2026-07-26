from unittest.mock import Mock

from sentinel_agent.windows_control import WindowsController


def test_bound_window_is_reused_without_rediscovery():
    controller = WindowsController()
    window = Mock()
    window.window_text.return_value = "Calculator"
    window.is_visible.return_value = True
    controller._bound_windows["calculator"] = window
    assert controller._window("calculator") is window
