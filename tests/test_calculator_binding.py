from sentinel_agent.windows_control import WindowsController


def test_packaged_calculator_host_is_allowlisted():
    assert "applicationframehost.exe" in WindowsController.process_names["calculator"]
