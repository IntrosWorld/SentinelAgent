"""Pure-fixture benchmark checks: no model, network, or native desktop access."""

import asyncio
import base64
from contextlib import nullcontext
from io import BytesIO
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from benchmark import desktop_probe as probe_module
from sentinel_agent.computer_use import desktop_planner as planner_module
from sentinel_agent.computer_use.desktop_schema import DesktopAction
from sentinel_agent.egress.client import EgressError


@pytest.fixture
def snapshot_file(tmp_path):
    image = BytesIO()
    Image.new("RGB", (100, 80), "white").save(image, format="PNG")
    value = {
        "id": "fixture-snapshot", "window_id": "100", "app": "fixture.exe", "title": "Test fixture",
        "width": 100, "height": 80, "text": "Document text",
        "elements": [
            {"id": "button-1", "role": "Button", "name": "Apply", "bounds": [10, 10, 30, 30]},
            {"id": "field-1", "role": "Edit", "name": "Content", "bounds": [40, 10, 90, 30], "editable": True},
        ],
        "screenshot": base64.b64encode(image.getvalue()).decode("ascii"),
    }
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def transport(monkeypatch):
    state = SimpleNamespace(
        calls=[], gateways=[], gateway_kwargs=[], response={
            "message": {"content": json.dumps({"kind": "click", "snapshot_id": "fixture-snapshot", "x": 20, "y": 20})},
            "eval_count": 12,
        }, error=None, start_error=None, close_error=None,
    )

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            state.calls.append((url, kwargs))
            if state.error is not None:
                raise state.error
            return SimpleNamespace(json=lambda: state.response)

    class Gateway:
        def __init__(self, **kwargs):
            self.audit = SimpleNamespace(path="fixture-audit.jsonl")
            self.started = self.closed = 0
            state.gateways.append(self)
            state.gateway_kwargs.append(kwargs)

        def start(self):
            self.started += 1
            if state.start_error:
                raise state.start_error
            return self

        def activate(self):
            return nullcontext()

        def close(self):
            self.closed += 1
            if state.close_error:
                raise state.close_error

    monkeypatch.setattr(planner_module, "EgressClient", Client)
    monkeypatch.setattr(probe_module, "EgressGateway", Gateway)
    return state


def set_response(transport, kind="click", **fields):
    transport.response["message"]["content"] = json.dumps({"kind": kind, "snapshot_id": "fixture-snapshot", **fields})


async def test_original_four_positional_arguments_remain_compatible(snapshot_file, transport):
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5)
    assert result["status"] == "proposed"
    assert result["input_mode"] == "vision"
    assert result["coordinate_mode"] == "pixels"
    assert result["action_coordinates"] == "window_pixels"
    assert result["model_metrics"] == {"eval_count": 12}
    assert "target_match" not in result
    assert transport.gateways[0].started == transport.gateways[0].closed == 1
    assert len(transport.calls) == 1


@pytest.mark.parametrize("point,match", [((10, 10), True), ((29, 29), True), ((30, 20), False), ((20, 30), False), ((9, 20), False)])
async def test_target_box_scores_independently_in_window_pixels(snapshot_file, transport, point, match):
    set_response(transport, x=point[0], y=point[1])
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, expect_box=(10, 10, 30, 30))
    assert result["status"] == "proposed"
    assert result["target_match"] is match
    assert result["expected_target"] == {"box": [10, 10, 30, 30], "kind": "click"}


@pytest.mark.parametrize("kind,fields", [
    ("finish", {}), ("wait", {}), ("hover", {"x": 20, "y": 20}),
    ("press_key", {"key": "ENTER"}),
])
async def test_valid_json_wrong_kind_does_not_pass_click_benchmark(snapshot_file, transport, kind, fields):
    set_response(transport, kind, **fields)
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, expect_box=(10, 10, 30, 30))
    assert result["status"] == "proposed"
    assert result["target_match"] is False


@pytest.mark.parametrize("target,expected", [("button-1", True), ("field-1", False)])
async def test_expected_element_requires_exact_observed_id(snapshot_file, transport, target, expected):
    set_response(transport, element_id=target)
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, expect_element="button-1")
    assert result["status"] == "proposed"
    assert result["target_match"] is expected


async def test_coordinate_click_does_not_count_as_exact_element_id_match(snapshot_file, transport):
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, expect_element="button-1")
    assert result["target_match"] is False


@pytest.mark.parametrize("box,match", [((10, 10, 30, 30), True), ((40, 10, 90, 30), False)])
async def test_both_expectations_require_both_matches_and_score_element_center(snapshot_file, transport, box, match):
    set_response(transport, element_id="button-1")
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5,
                                      expect_box=box, expect_element="button-1")
    assert result["target_match"] is match


async def test_expected_nonclick_kind_can_be_scored(snapshot_file, transport):
    set_response(transport, "type", element_id="field-1", text="Hi")
    result = await probe_module.probe(snapshot_file, "Enter Hi", "fixture-model", 5,
                                      expect_element="field-1", expect_kind="type")
    assert result["target_match"] is True


async def test_ground_truth_is_absent_from_model_payload(snapshot_file, transport):
    set_response(transport, element_id="button-1")
    await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5,
                             expect_box=(17, 18, 26, 27), expect_element="button-1", expect_kind="hover")
    payload = transport.calls[0][1]["json"]
    encoded = json.dumps(payload)
    for forbidden in ("expected_target", "expect_box", "expect_element", "expect_kind", "target_match"):
        assert forbidden not in encoded
    assert "[17, 18, 26, 27]" not in encoded


async def test_qwen_normalized_output_is_scored_after_one_pixel_conversion(snapshot_file, transport):
    set_response(transport, x=500, y=500)
    result = await probe_module.probe(snapshot_file, "Select the control", "qwen3-vl:2b-instruct", 5,
                                      expect_box=(49, 39, 52, 42))
    assert result["coordinate_mode"] == "normalized_1000"
    assert result["action"] == {"kind": "click", "snapshot_id": "fixture-snapshot", "x": 50, "y": 40}
    assert result["target_match"] is True


async def test_explicit_pixel_coordinate_override_reaches_planner(snapshot_file, transport):
    result = await probe_module.probe(snapshot_file, "Select the control", "qwen3-vl:2b-instruct", 5,
                                      coordinates="pixels", expect_box=(10, 10, 30, 30))
    assert result["coordinate_mode"] == "pixels"
    assert result["action"]["x"] == 20
    assert result["target_match"] is True


@pytest.mark.parametrize("base_url,host,port", [("http://localhost:18094", "127.0.0.1", 18094),
                                               ("http://127.0.0.1:11435", "127.0.0.1", 11435),
                                               ("http://[::1]:18094", "::1", 18094)])
async def test_gateway_only_grants_exact_canonical_profile_endpoint(snapshot_file, transport, base_url, host, port):
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, base_url=base_url)
    assert result["status"] == "proposed"
    config = transport.gateway_kwargs[0]["policy"].config
    assert config.local_endpoints == frozenset({(host, port, "http")})
    assert config.approved_sites == config.provider_hosts == frozenset()
    assert transport.gateway_kwargs[0]["enabled"] is True
    assert transport.calls[0][0].endswith(f":{port}/api/chat")


async def test_no_vision_uses_real_accessibility_observations_and_sends_no_pixels(snapshot_file, transport):
    set_response(transport, element_id="button-1")
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5,
                                      no_vision=True, expect_element="button-1")
    assert result["input_mode"] == "accessibility_only"
    assert result["target_match"] is True
    payload = transport.calls[0][1]["json"]
    assert all("images" not in message for message in payload["messages"])
    assert "screenshot" not in json.dumps(payload)


async def test_no_vision_rejects_image_only_fixture_before_gateway(snapshot_file, transport):
    value = json.loads(snapshot_file.read_text(encoding="utf-8"))
    value.update(text="", elements=[])
    snapshot_file.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="actual text or control"):
        await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, no_vision=True)
    assert transport.gateways == transport.calls == []


async def test_ocr_mode_is_forwarded_without_running_ocr_in_this_test(snapshot_file, transport, monkeypatch):
    captured = {}
    original = probe_module.DesktopPlanner
    def planner(profile, **kwargs):
        captured.update(kwargs)
        # Only observe argument forwarding; no local OCR executable is started.
        return original(profile, vision=kwargs["vision"], ocr=False)
    monkeypatch.setattr(probe_module, "DesktopPlanner", planner)
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, ocr=True)
    assert captured == {"vision": True, "ocr": True}
    assert result["ocr"] is True


@pytest.mark.parametrize("overrides", [
    {"expect_box": (-1, 0, 30, 30)}, {"expect_box": (0, 0, 101, 30)},
    {"expect_box": (0, 0, 30, 81)}, {"expect_box": (0, 0, 0, 30)},
    {"expect_box": (False, 0, 30, 30)}, {"expect_box": (0, 0, 30.0, 30)},
    {"expect_box": (0, 0, 30)}, {"expect_element": "invented"},
    {"expect_kind": "drag", "expect_box": (10, 10, 30, 30)},
    {"expect_kind": "finish", "expect_element": "button-1"},
    {"expect_kind": "shell"}, {"coordinates": "guess"}, {"no_vision": True, "ocr": True},
    {"base_url": "https://remote.example:443"}, {"base_url": "http://192.168.1.2:11434"},
    {"base_url": "http://user:secret@127.0.0.1:11434"}, {"base_url": "http://127.0.0.1:11434\n"},
])
async def test_bad_configuration_never_starts_gateway_or_model(snapshot_file, transport, overrides):
    try:
        result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, **overrides)
    except ValueError:
        pass
    else:
        assert result["status"] == "failed"
    assert transport.gateways == transport.calls == []


@pytest.mark.parametrize("model", ["large:cloud", "large:cloud-latest", "large:120b-cloud", "large-cloud:latest", "LARGE:CLOUD"])
async def test_cloud_tags_are_rejected_before_any_gateway(snapshot_file, transport, model):
    with pytest.raises(ValueError, match="Cloud model tags"):
        await probe_module.probe(snapshot_file, "Select the control", model, 5)
    assert transport.gateways == transport.calls == []


@pytest.mark.parametrize("timeout", [True, 0, 901, float("nan"), float("inf")])
async def test_invalid_timeouts_are_preflight_errors(snapshot_file, transport, timeout):
    with pytest.raises(ValueError):
        await probe_module.probe(snapshot_file, "Select the control", "fixture-model", timeout)
    assert transport.gateways == transport.calls == []


def test_snapshot_read_is_bounded_even_if_file_is_large_or_growing(monkeypatch):
    sizes = []
    class Source(BytesIO):
        def read(self, size=-1):
            sizes.append(size)
            return b"x" * size
    monkeypatch.setattr(probe_module, "MAX_SNAPSHOT_BYTES", 32)
    monkeypatch.setattr(probe_module, "Path", lambda _: SimpleNamespace(open=lambda mode: Source()))
    with pytest.raises(ValueError, match="Snapshot too large"):
        probe_module._load_snapshot("unused")
    assert sizes == [33]


async def test_existing_evidence_prevents_inference_and_is_not_overwritten(snapshot_file, transport, tmp_path):
    destination = tmp_path / "evidence.json"
    destination.write_text("Preserve this evidence", encoding="utf-8")
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, output=destination)
    assert result["status"] == "failed"
    assert result["failure_stage"] == "output-preflight"
    assert result["error_type"] == "FileExistsError"
    assert transport.calls == transport.gateways == []
    assert destination.read_text(encoding="utf-8") == "Preserve this evidence"


async def test_output_is_reserved_before_inference_and_records_final_result(snapshot_file, transport, tmp_path, monkeypatch):
    destination = tmp_path / "evidence.json"
    original = probe_module.DesktopPlanner
    class InspectingPlanner(original):
        async def next_action(self, *args):
            assert destination.exists()
            with pytest.raises(FileExistsError):
                destination.open("x")
            return await super().next_action(*args)
    monkeypatch.setattr(probe_module, "DesktopPlanner", InspectingPlanner)
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5,
                                      expect_box=(10, 10, 30, 30), output=destination)
    assert json.loads(destination.read_text(encoding="utf-8")) == result
    assert result["target_match"] is True
    assert transport.gateways[0].closed == 1


@pytest.mark.parametrize("error,error_type", [(EgressError("secret transport body"), "DesktopPlanningError"),
                                            (TimeoutError("secret timeout endpoint"), "TimeoutError")])
async def test_transport_failures_are_typed_sanitized_and_closed(snapshot_file, transport, error, error_type):
    transport.error = error
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5)
    assert result["status"] == "failed"
    assert result["failure_stage"] == "model-request"
    assert result["error_type"] == error_type
    assert "secret" not in json.dumps(result)
    assert transport.gateways[0].closed == 1


async def test_cancellation_is_reraised_and_writes_cancelled_evidence(snapshot_file, transport, tmp_path):
    destination = tmp_path / "cancelled-evidence.json"
    transport.error = asyncio.CancelledError("secret cancellation details")
    with pytest.raises(asyncio.CancelledError):
        await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5,
                                 expect_box=(10, 10, 30, 30), output=destination)
    result = json.loads(destination.read_text(encoding="utf-8"))
    assert result["status"] == "cancelled"
    assert result["error_type"] == "CancelledError"
    assert result["failure_stage"] == "model-request"
    assert result["target_match"] is None
    assert "action" not in result
    assert "secret" not in json.dumps(result)
    assert transport.gateways[0].closed == 1


async def test_gateway_start_failure_still_closes_created_gateway(snapshot_file, transport):
    transport.start_error = RuntimeError("secret startup error")
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5)
    assert result["failure_stage"] == "gateway-start"
    assert result["error_type"] == "RuntimeError"
    assert transport.gateways[0].closed == 1
    assert transport.calls == []
    assert "secret" not in json.dumps(result)


async def test_model_constructor_failure_starts_no_gateway(snapshot_file, transport, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("secret constructor error")
    monkeypatch.setattr(probe_module, "DesktopPlanner", broken)
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5)
    assert result["status"] == "failed"
    assert result["failure_stage"] == "configuration"
    assert transport.gateways == transport.calls == []
    assert "secret" not in json.dumps(result)


async def test_cleanup_failure_is_typed_without_raw_details(snapshot_file, transport):
    transport.close_error = RuntimeError("secret cleanup error")
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5)
    assert result["status"] == "failed"
    assert result["cleanup_error_type"] == "RuntimeError"
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("action_text", ["Not JSON", '{"kind":"click","snapshot_id":"fixture-snapshot","x":100,"y":20}'])
async def test_invalid_proposals_cannot_be_scored_as_success(snapshot_file, transport, action_text):
    transport.response["message"]["content"] = action_text
    result = await probe_module.probe(snapshot_file, "Select the control", "fixture-model", 5, expect_box=(10, 10, 30, 30))
    assert result["status"] == "failed"
    assert result["target_match"] is None


@pytest.mark.parametrize("status,match,exit_code", [("proposed", True, 0), ("proposed", False, 1), ("failed", None, 1)])
def test_cli_exit_code_fails_a_target_mismatch(monkeypatch, capsys, status, match, exit_code):
    captured = {}
    async def fake_probe(*args, **kwargs):
        captured.update(kwargs)
        return {"status": status, "target_match": match}
    monkeypatch.setattr(probe_module, "probe", fake_probe)
    monkeypatch.setattr("sys.argv", ["desktop_probe", "snapshot.json", "Select the control", "--coordinates", "pixels",
                                    "--expect-box", "10", "10", "30", "30", "--expect-kind", "hover", "--ocr"])
    assert probe_module.main() == exit_code
    assert json.loads(capsys.readouterr().out)["target_match"] is match
    assert captured["coordinates"] == "pixels"
    assert captured["expect_kind"] == "hover"
    assert captured["expect_box"] == [10, 10, 30, 30]
    assert captured["ocr"] is True


def test_cli_preflight_error_prints_only_exception_type(monkeypatch, capsys):
    async def failed_probe(*args, **kwargs):
        raise ValueError("secret fixture content")
    monkeypatch.setattr(probe_module, "probe", failed_probe)
    monkeypatch.setattr("sys.argv", ["desktop_probe", "snapshot.json", "Select the control"])
    assert probe_module.main() == 1
    output = capsys.readouterr().out
    assert "secret" not in output
    assert json.loads(output)["error_type"] == "ValueError"
