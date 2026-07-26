from sentinel_agent.planner import HybridPlanner


def test_click_instruction_maps_to_inspect_then_click():
    plan = HybridPlanner._fallback_plan("Open calculator and click Seven button")
    assert [action.name for action in plan.actions] == [
        "open_app",
        "inspect_app",
        "click_control",
    ]
    assert plan.actions[-1].arguments["target"] == "Seven"


def test_key_instruction_maps_to_allowlisted_keypress():
    plan = HybridPlanner._fallback_plan("Open notepad and press enter")
    assert plan.actions[-1].name == "press_key"
    assert plan.actions[-1].arguments["key"] == "ENTER"
