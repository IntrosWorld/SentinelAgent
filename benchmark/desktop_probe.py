"""Run a real local vision model on a saved window, without sending any input.

This measures perception/planning only. It is deliberately separate from the
native executor so a model's proposed coordinates cannot become an auto-click.
"""
import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import time

from sentinel_agent.computer_use.desktop_planner import DesktopPlanner
from sentinel_agent.computer_use.desktop_schema import DesktopAction, DesktopSnapshot, validate_desktop_action
from sentinel_agent.egress.client import EgressGateway
from sentinel_agent.egress.config import EgressConfig, is_local_host, parse_destination
from sentinel_agent.egress.policy import DestinationPolicy
from sentinel_agent.egress.proxy import ProxyLimits
from sentinel_agent.models import ModelProfile


MAX_SNAPSHOT_BYTES = 12 * 1024 * 1024
TARGET_KINDS = ("click", "double_click", "hover", "type", "scroll")
COORDINATE_MODES = ("auto", "pixels", "normalized_1000")


def _load_snapshot(snapshot_path):
    # A stat check alone races a growing file. Bound the actual read as well.
    with Path(snapshot_path).open("rb") as source:
        raw = source.read(MAX_SNAPSHOT_BYTES + 1)
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot too large")
    return raw, DesktopSnapshot.model_validate_json(raw)


def _expectations(snapshot, expect_box, expect_element, expect_kind):
    if expect_kind not in TARGET_KINDS:
        # A single target box cannot meaningfully score a drag's two endpoints,
        # or untargeted finish/wait/keyboard actions. Fail instead of rewarding
        # a valid JSON action that does not perform the benchmarked operation.
        raise ValueError("Expected action kind must identify a single target")
    expected = {}
    if expect_box is not None:
        if (not isinstance(expect_box, (tuple, list)) or len(expect_box) != 4
                or any(type(value) is not int for value in expect_box)):
            raise ValueError("Expected target box requires four integer pixel bounds")
        left, top, right, bottom = expect_box
        if not (0 <= left < right <= snapshot.width and 0 <= top < bottom <= snapshot.height):
            raise ValueError("Expected target box must fit inside the snapshot")
        expected["box"] = [left, top, right, bottom]
    if expect_element is not None:
        if not isinstance(expect_element, str) or expect_element not in {element.id for element in snapshot.elements}:
            raise ValueError("Expected element must exist in the snapshot")
        expected["element_id"] = expect_element
    if expected:
        expected["kind"] = expect_kind
    return expected


def _target_matches(action, snapshot, expected):
    """Score canonical window pixels; evaluation labels never enter the prompt."""
    if action.kind != expected["kind"]:
        return False
    if "element_id" in expected and action.element_id != expected["element_id"]:
        return False
    if "box" in expected:
        if action.element_id is not None:
            target = next((element for element in snapshot.elements if element.id == action.element_id), None)
            if target is None:
                return False
            x, y = (target.bounds[0] + target.bounds[2]) // 2, (target.bounds[1] + target.bounds[3]) // 2
        else:
            x, y = action.x, action.y
        if x is None or y is None:
            return False
        left, top, right, bottom = expected["box"]
        if not (left <= x < right and top <= y < bottom):
            return False
    return True


async def probe(snapshot_path, goal, model, timeout, *, coordinates=None, no_vision=False,
                base_url="http://127.0.0.1:11434", expect_box=None, expect_element=None,
                expect_kind="click", output=None, ocr=False):
    """Propose, independently score, and record one action without executing it.

    The original four positional arguments remain compatible. All benchmark
    controls are keyword-only. Output is exclusively reserved before inference;
    an existing evidence file is never overwritten or needlessly rerun.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 900:
        raise ValueError("Timeout must be between 1 and 900 seconds")
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("A nonblank goal is required")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("A local model name is required")
    if "-cloud" in model.casefold() or ":cloud" in model.casefold():
        raise ValueError("Cloud model tags are not permitted by the local benchmark")
    if coordinates is not None and coordinates not in COORDINATE_MODES:
        raise ValueError("Unknown coordinate mode")
    if ocr and no_vision:
        raise ValueError("OCR requires a vision input fixture")
    # Validate controls before urlsplit can silently remove them. The planner
    # additionally canonicalizes localhost to a literal loopback destination.
    host, _, _ = parse_destination(base_url)
    if not is_local_host(host):
        raise ValueError("Benchmark model endpoint must be loopback-only")
    raw, snapshot = _load_snapshot(snapshot_path)
    expected = _expectations(snapshot, expect_box, expect_element, expect_kind)
    if no_vision and not snapshot.text.strip() and not any(
            not element.password and (element.name.strip() or element.editable) for element in snapshot.elements):
        raise ValueError("Accessibility-only evaluation requires actual text or control observations")

    started = time.monotonic()
    planner, gateway, destination = None, None, None
    result = {"mode": "proposal_only_no_input", "model": model,
              "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
              "snapshot_id": snapshot.id, "window_size": [snapshot.width, snapshot.height],
              "element_count": len(snapshot.elements), "goal": goal,
              "input_mode": "accessibility_only" if no_vision else "vision",
              "ocr": bool(ocr), "action_coordinates": "window_pixels"}
    if expected:
        result.update(expected_target=expected, target_match=None)
    stage = "configuration"
    try:
        profile = ModelProfile(name="desktop-probe", provider="ollama", model=model,
                               base_url=base_url, timeout=timeout, vision=not no_vision,
                               desktop_coordinates=coordinates or "auto")
        planner = DesktopPlanner(profile, vision=not no_vision, ocr=ocr)
        result["coordinate_mode"] = planner.coordinate_mode
        canonical_profile = profile.model_copy(update={"base_url": planner.base_url})
        endpoint_config = EgressConfig.from_profiles([canonical_profile], approved_sites=())
        # No settings/profile mutations or inherited grants to other local ports.
        if endpoint_config.provider_hosts or len(endpoint_config.local_endpoints) != 1:
            raise ValueError("Benchmark model endpoint must resolve to one loopback service")
        if output is not None:
            stage = "output-preflight"
            destination = Path(output).open("x", encoding="utf-8")
        stage = "gateway-start"
        gateway = EgressGateway(enabled=True, policy=DestinationPolicy(endpoint_config),
                                proxy_limits=ProxyLimits(idle_timeout=timeout))
        gateway.start()
        stage = "model-request"
        with gateway.activate():
            action = await asyncio.wait_for(planner.next_action(goal, snapshot, []), timeout=timeout)
        stage = "action-validation"
        action = DesktopAction.model_validate(action)
        validate_desktop_action(action, snapshot)
        result.update(status="proposed", action=action.model_dump(exclude_none=True))
        if expected:
            result["target_match"] = _target_matches(action, snapshot, expected)
    except asyncio.CancelledError:
        # Preserve cancellation semantics while leaving complete evidence for
        # the interrupted request. Finally still closes the gateway and output.
        result.update(status="cancelled", error_type="CancelledError", failure_stage=stage)
        raise
    except Exception as exc:
        # Exception values can contain prompts, documents, endpoints or tokens.
        result.update(status="failed", error_type=type(exc).__name__, failure_stage=stage)
    finally:
        result["seconds"] = round(time.monotonic() - started, 2)
        result["model_metrics"] = getattr(planner, "last_metrics", {})
        if gateway is not None:
            result["egress_log"] = str(gateway.audit.path)
            try:
                gateway.close()
            except Exception as exc:
                result.update(status="failed", cleanup_error_type=type(exc).__name__)
        if destination is not None:
            try:
                destination.write(json.dumps(result, indent=2, ensure_ascii=True) + "\n")
            finally:
                destination.close()
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("goal")
    parser.add_argument("--model", default="qwen3-vl:2b-instruct")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--coordinates", choices=COORDINATE_MODES, help="Model output convention; scored actions always use window pixels")
    parser.add_argument("--no-vision", action="store_true", help="Evaluate actual accessibility observations without sending screenshot pixels")
    parser.add_argument("--ocr", action="store_true", help="Add locally extracted screenshot text labels; requires vision mode")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434", help="Local Ollama endpoint only; never grants another model port")
    parser.add_argument("--expect-box", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                        help="Independent target box in window pixels, never shown to the model")
    parser.add_argument("--expect-element", help="Independent exact observed element ID, never shown to the model as ground truth")
    parser.add_argument("--expect-kind", choices=TARGET_KINDS, default="click", help="Required action kind when scoring a target")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = asyncio.run(probe(args.snapshot, args.goal, args.model, args.timeout,
                                  coordinates=args.coordinates, no_vision=args.no_vision,
                                  base_url=args.base_url, expect_box=args.expect_box,
                                  expect_element=args.expect_element, expect_kind=args.expect_kind,
                                  output=args.output, ocr=args.ocr))
    except (ValueError, OSError, RuntimeError) as exc:
        result = {"status": "failed", "failure_stage": "preflight", "error_type": type(exc).__name__}
    encoded = json.dumps(result, indent=2, ensure_ascii=True)
    print(encoded)
    return 0 if result["status"] == "proposed" and result.get("target_match", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
