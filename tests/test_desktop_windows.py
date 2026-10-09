"""Mock-only backend tests. Never launch, inspect, focus, or control a real app."""
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

from PIL import Image
import pytest

from sentinel_agent.computer_use.desktop_schema import DesktopAction, DesktopFailure, StaleDesktopObservation
from sentinel_agent.computer_use.desktop_windows import WindowsDesktopSession, _blocked


def rectangle(left=10, top=20, right=310, bottom=220):
    return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class Control:
    def __init__(self, role="Button", name="Rectangle", value="", editable=False, password=False, runtime_id=2):
        self.element_info = SimpleNamespace(
            control_type=role, name=name, automation_id="fixture-control",
            element=SimpleNamespace(CurrentIsPassword=password, GetRuntimeId=lambda: (1, runtime_id)),
        )
        self.iface_value = SimpleNamespace(CurrentValue=value, CurrentIsReadOnly=not editable)
        self.is_visible = Mock(return_value=True)
        self.is_enabled = Mock(return_value=True)
        self.has_keyboard_focus = Mock(return_value=True)
        self.rectangle = Mock(return_value=rectangle(30, 50, 130, 90))
        self.window_text = Mock(return_value=name)


@pytest.fixture
def rig():
    control = Control()
    window = SimpleNamespace(
        handle=123, process_id=Mock(return_value=7), window_text=Mock(return_value="Untitled - Example"),
        is_visible=Mock(return_value=True), is_enabled=Mock(return_value=True),
        set_focus=Mock(), rectangle=Mock(return_value=rectangle()), descendants=Mock(return_value=[control]),
        element_info=SimpleNamespace(element=SimpleNamespace(GetRuntimeId=lambda: (1, 123))),
    )
    process = SimpleNamespace(create_time=Mock(return_value=1.25), name=Mock(return_value="example.exe"))
    api = SimpleNamespace(
        pixels=lambda: nullcontext(), foreground=Mock(return_value=True), popup=Mock(return_value=False),
        modifiers_released=Mock(return_value=True), rect=Mock(return_value=(10, 20, 310, 220)),
        screen=Mock(return_value=(0, 0, 1000, 1000)), uncovered=Mock(return_value=True),
        point_window=Mock(return_value=123), dpi=Mock(return_value=96), type_unicode=Mock(),
        is_elevated=Mock(return_value=False),
    )
    desktop = SimpleNamespace(window=Mock(return_value=SimpleNamespace(wrapper_object=lambda: window)),
                              windows=Mock(return_value=[window]))
    dependencies = SimpleNamespace(
        Desktop=Mock(return_value=desktop), psutil=SimpleNamespace(Process=Mock(return_value=process)),
        mouse=Mock(), keyboard=Mock(), api=api,
        grab=Mock(side_effect=lambda **kwargs: Image.new("RGB", (300, 200), "white")),
    )
    session = WindowsDesktopSession(123)
    session._deps = dependencies
    return SimpleNamespace(session=session, window=window, control=control, process=process,
                           api=api, dependencies=dependencies)


async def opened(rig):
    await rig.session.start()
    return await rig.session.observe()


async def test_controller_window_cannot_approve_itself(rig, monkeypatch):
    monkeypatch.setenv("SENTINEL_DESKTOP_HOST_PID", "7")
    with pytest.raises(DesktopFailure, match="own approval window"):
        await rig.session.start()
    rig.window.set_focus.assert_not_called()


def test_window_picker_excludes_controller(rig, monkeypatch):
    monkeypatch.setenv("SENTINEL_DESKTOP_HOST_PID", "7")
    monkeypatch.setattr(WindowsDesktopSession, "_libraries", lambda _: rig.dependencies)
    assert WindowsDesktopSession.list_windows() == []


def action(snapshot, kind="click", **fields):
    if not fields and kind in {"click", "double_click", "hover", "type"}:
        fields["element_id"] = snapshot.elements[0].id
    return DesktopAction(kind=kind, snapshot_id=snapshot.id, **fields)


async def test_observation_is_window_relative_and_randomized(rig):
    first = await opened(rig)
    second = await rig.session.observe()
    assert first.window_id == "123"
    assert first.elements[0].bounds == (20, 30, 120, 70)
    assert (first.width, first.height) == (300, 200)
    assert first.screenshot.startswith("iVBOR")
    assert first.id != second.id
    assert first.elements[0].id != second.elements[0].id
    assert first.text == ""  # Not the title, nor a button caption.
    rig.dependencies.grab.assert_called_with(bbox=(10, 20, 310, 220), all_screens=True)


async def test_only_document_text_and_field_values_become_success_evidence(rig):
    rig.window.descendants.return_value = [Control("Button", "Completed"), Control("Edit", "Name", "Ada", True),
                                          Control("Text", "Ready"), Control("Document", "Document", "hello")]
    snapshot = await opened(rig)
    assert snapshot.text == "Ada\nhello"


async def test_static_and_button_labels_are_not_completion_evidence(rig):
    rig.window.descendants.return_value = [Control("Text", "Saved"), Control("Button", "Saved")]
    snapshot = await opened(rig)
    assert snapshot.text == ""
    assert [element.name for element in snapshot.elements] == ["Saved", "Saved"]


async def test_click_uses_observed_target_and_consumes_snapshot(rig):
    snapshot = await opened(rig)
    proposal = action(snapshot)
    await rig.session.validate(proposal)
    await rig.session.act(proposal)
    rig.dependencies.mouse.click.assert_called_once_with(coords=(80, 70))
    with pytest.raises(StaleDesktopObservation):
        await rig.session.act(proposal)
    assert rig.dependencies.mouse.click.call_count == 1


@pytest.mark.parametrize("mutation", ["title", "position", "control", "value", "dpi", "pixels", "runtime_id"])
async def test_freshness_rejects_change_without_dispatch(rig, mutation):
    snapshot = await opened(rig)
    if mutation == "title":
        rig.window.window_text.return_value = "Different document"
    elif mutation == "position":
        rig.window.rectangle.return_value = rectangle(20, 20, 320, 220)
        rig.api.rect.return_value = (20, 20, 320, 220)
    elif mutation == "control":
        rig.control.element_info.name = "Delete"
    elif mutation == "value":
        rig.control.element_info.control_type = "Edit"
        rig.control.iface_value.CurrentValue = "Changed"
    elif mutation == "dpi":
        rig.api.dpi.return_value = 144
    elif mutation == "runtime_id":
        rig.control.element_info.element.GetRuntimeId = lambda: (1, 555)
    else:
        rig.dependencies.grab.side_effect = lambda **kwargs: Image.new("RGB", (300, 200), "black")
    with pytest.raises(StaleDesktopObservation):
        await rig.session.act(action(snapshot))
    assert not rig.dependencies.mouse.mock_calls


@pytest.mark.parametrize("guard", ["focus", "popup", "covered", "point", "process", "modifiers", "hidden", "dpi-mismatch"])
async def test_native_guards_stop_input(rig, guard):
    snapshot = await opened(rig)
    if guard == "focus":
        rig.api.foreground.return_value = False
    elif guard == "popup":
        rig.api.popup.return_value = True
    elif guard == "covered":
        rig.api.uncovered.return_value = False
    elif guard == "point":
        rig.api.point_window.return_value = 999
    elif guard == "process":
        rig.process.create_time.return_value = 2.0
    elif guard == "modifiers":
        rig.api.modifiers_released.return_value = False
    elif guard == "hidden":
        rig.window.is_visible.return_value = False
    else:
        rig.api.rect.return_value = (0, 0, 450, 300)
    with pytest.raises(DesktopFailure):
        await rig.session.act(action(snapshot))
    assert not rig.dependencies.mouse.mock_calls
    rig.window.set_focus.assert_not_called()


@pytest.mark.parametrize("kind,fields", [("click", {"x": 300, "y": 10}),
                                        ("click", {"element_id": "invented"}),
                                        ("type", {"element_id": "invented", "text": "hi"})])
async def test_invalid_targets_never_dispatch(rig, kind, fields):
    snapshot = await opened(rig)
    with pytest.raises(DesktopFailure):
        await rig.session.act(action(snapshot, kind, **fields))
    assert not rig.dependencies.mouse.mock_calls


async def test_password_field_is_never_read_or_captured(rig):
    rig.control.element_info.element.CurrentIsPassword = True
    await rig.session.start()
    with pytest.raises(DesktopFailure, match="Password"):
        await rig.session.observe()
    rig.control.window_text.assert_not_called()
    rig.dependencies.grab.assert_not_called()


@pytest.mark.parametrize("app,title", [("cmd.exe", "Anything"), ("example.exe", "Sign in"),
                                       ("codex.exe", "Code"), ("bitwarden.exe", "Vault"),
                                       ("example.exe", "Windows Security")])
async def test_sensitive_app_scope_rejected_before_focus(rig, app, title):
    rig.process.name.return_value = app
    rig.window.window_text.return_value = title
    with pytest.raises(DesktopFailure, match="scope"):
        await rig.session.start()
    rig.window.set_focus.assert_not_called()


async def test_type_uses_literal_unicode_not_key_grammar(rig):
    rig.control.element_info.control_type = "Edit"
    rig.control.iface_value.CurrentIsReadOnly = False
    snapshot = await opened(rig)
    text = "{ENTER} + ^ % naïve हिन्दी 😀"
    await rig.session.act(action(snapshot, "type", element_id=snapshot.elements[0].id, text=text))
    rig.api.type_unicode.assert_called_once_with(text)
    rig.dependencies.keyboard.send_keys.assert_not_called()


async def test_type_stops_if_target_not_focused(rig):
    rig.control.element_info.control_type = "Edit"
    rig.control.iface_value.CurrentIsReadOnly = False
    rig.control.has_keyboard_focus.return_value = False
    snapshot = await opened(rig)
    with pytest.raises(DesktopFailure, match="focus"):
        await rig.session.act(action(snapshot, "type", element_id=snapshot.elements[0].id, text="hello"))
    rig.api.type_unicode.assert_not_called()
    assert rig.session._snapshot is None


async def test_uncertain_dispatch_consumes_ids(rig):
    snapshot = await opened(rig)
    rig.dependencies.mouse.click.side_effect = RuntimeError("A native failure with private details")
    with pytest.raises(DesktopFailure, match="uncertain") as error:
        await rig.session.act(action(snapshot))
    assert "private details" not in str(error.value)
    with pytest.raises(StaleDesktopObservation):
        await rig.session.act(action(snapshot))
    assert rig.dependencies.mouse.click.call_count == 1


async def test_drag_releases_mouse_after_partial_failure(rig, monkeypatch):
    snapshot = await opened(rig)
    rig.dependencies.mouse.move.side_effect = RuntimeError("failed")
    monkeypatch.setattr("sentinel_agent.computer_use.desktop_windows.time.sleep", lambda _: None)
    with pytest.raises(DesktopFailure, match="uncertain"):
        await rig.session.act(action(snapshot, "drag", x=30, y=50, end_x=100, end_y=150))
    rig.dependencies.mouse.release.assert_called_once_with(button="left")
    assert rig.session._snapshot is None


async def test_no_screenshot_mode_exposes_no_image_and_disallows_pixel_actions(rig):
    rig.session.capture_images = False
    snapshot = await opened(rig)
    assert snapshot.screenshot is None
    rig.dependencies.grab.assert_not_called()
    with pytest.raises(DesktopFailure, match="screenshot"):
        await rig.session.act(action(snapshot, x=50, y=50))
    await rig.session.act(action(snapshot))  # Observed UIA IDs still work.


async def test_focus_only_change_rejects_untargeted_key_in_uia_mode(rig):
    rig.session.capture_images = False
    first = Control("Edit", "First", editable=True, runtime_id=2)
    second = Control("Edit", "Second", editable=True, runtime_id=3)
    second.has_keyboard_focus.return_value = False
    rig.window.descendants.return_value = [first, second]
    snapshot = await opened(rig)
    first.has_keyboard_focus.return_value = False
    second.has_keyboard_focus.return_value = True
    with pytest.raises(StaleDesktopObservation):
        await rig.session.act(action(snapshot, "press_key", key="DELETE"))
    rig.dependencies.keyboard.send_keys.assert_not_called()


async def test_capture_dimension_mismatch_rejected(rig):
    rig.dependencies.grab.side_effect = lambda **kwargs: Image.new("RGB", (450, 300), "white")
    await rig.session.start()
    with pytest.raises(DesktopFailure, match="dimensions"):
        await rig.session.observe()


async def test_close_releases_session_without_closing_window(rig):
    await opened(rig)
    await rig.session.close()
    assert rig.session._snapshot is None
    assert not rig.dependencies.mouse.mock_calls
    assert not rig.dependencies.keyboard.mock_calls
    with pytest.raises(DesktopFailure, match="not open"):
        await rig.session.observe()


async def test_fixed_key_mapping(rig):
    snapshot = await opened(rig)
    await rig.session.act(action(snapshot, "press_key", key="CTRL+A"))
    rig.dependencies.keyboard.send_keys.assert_called_once_with("^a", pause=0.02)


async def test_explicit_post_approval_focus_rechecks_identity_and_preserves_ids(rig):
    snapshot = await opened(rig)
    rig.api.foreground.return_value = False
    rig.window.set_focus.side_effect = lambda: setattr(rig.api.foreground, "return_value", True)
    await rig.session.focus()
    rig.window.set_focus.assert_called_once()
    assert rig.session._snapshot.id == snapshot.id
    await rig.session.act(action(snapshot))


async def test_explicit_focus_does_not_ignore_reused_process(rig):
    await opened(rig)
    rig.api.foreground.return_value = False
    rig.process.create_time.return_value = 999.0
    with pytest.raises(DesktopFailure, match="process changed"):
        await rig.session.focus()
    rig.window.set_focus.assert_not_called()


@pytest.mark.parametrize("which", ["target", "controller"])
async def test_elevated_sessions_are_rejected_before_focus(rig, which):
    rig.api.is_elevated.side_effect = lambda pid: (pid == 7) if which == "target" else (pid != 7)
    with pytest.raises(DesktopFailure, match="Elevated"):
        await rig.session.start()
    rig.window.set_focus.assert_not_called()
    rig.dependencies.grab.assert_not_called()


async def test_unknown_process_privileges_are_fail_closed(rig):
    rig.api.is_elevated.side_effect = DesktopFailure("Selected process privileges could not be established")
    with pytest.raises(DesktopFailure, match="privileges"):
        await rig.session.start()
    rig.window.set_focus.assert_not_called()


async def test_top_level_runtime_id_change_is_rejected(rig):
    snapshot = await opened(rig)
    rig.window.element_info.element.GetRuntimeId = lambda: (1, 777)
    with pytest.raises(DesktopFailure, match="process changed"):
        await rig.session.act(action(snapshot))
    assert not rig.dependencies.mouse.mock_calls


def test_scope_filters_are_not_an_app_allowlist():
    assert not _blocked("custom_drawing_app.exe", "My diagram")
    assert not _blocked("notepad.exe", "Untitled - Notepad")
    assert not _blocked("mspaint.exe", "Untitled - Paint")


def test_list_windows_omits_blocked_apps(rig, monkeypatch):
    monkeypatch.setattr(WindowsDesktopSession, "_libraries", lambda _: rig.dependencies)
    assert WindowsDesktopSession.list_windows() == [{"window_id": 123, "app": "example.exe", "title": "Untitled - Example"}]
    rig.process.name.return_value = "cmd.exe"
    assert WindowsDesktopSession.list_windows() == []
