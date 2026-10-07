import base64
import json

import pytest

from sentinel_agent.egress.dlp import DLPScanner, is_jwt, luhn, verhoeff


@pytest.mark.parametrize("detector,value", [
    ("private-key", "-----BEGIN RSA PRIVATE KEY-----\nsynthetic\n-----END RSA PRIVATE KEY-----"),
    ("openai-key", "sk-" + "a" * 30),
    ("google-key", "AIza" + "a" * 35),
    ("github-key", "ghp_" + "a" * 36),
    ("github-key", "github_pat_" + "a" * 30),
    ("aws-key", "AKIA" + "A" * 16),
    ("slack-key", "xoxb-" + "a" * 20),
    ("password", 'password="synthetic value"'),
    ("payment-card", "4111 1111 1111 1111"),
    ("aadhaar", "234567890124"),
    ("pan", "ABCDE1234F"),
    ("email", "person@example.com"),
    ("phone", "+1 202-555-0143"),
])
def test_detector_and_log_never_contain_value(detector, value):
    scanner = DLPScanner()
    result = scanner.protect({"prompt": value}, "approved-site")
    assert detector in [finding.detector for finding in result.findings]
    assert value not in repr(result)
    assert value not in json.dumps(result.summary())
    if result.allowed:
        assert value not in result.body["prompt"]


def test_validators_reject_invalid_numeric_data():
    assert luhn("4111111111111111") and not luhn("4111111111111112")
    assert not luhn("0000000000000000")
    assert verhoeff("234567890124") and not verhoeff("234567890125")
    assert not verhoeff("123456789012")
    result = DLPScanner().protect("4111111111111112 and 234567890125", "model-provider")
    assert result.allowed and not result.findings


def test_jwt_requires_json_header_payload_and_signature():
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    token = encode({"alg": "HS256"}) + "." + encode({"sub": "test"}) + ".c2ln"
    assert is_jwt(token)
    assert not is_jwt("abc.def.ghi") and not is_jwt("e30.e30.")
    result = DLPScanner().protect(token, "model-provider")
    assert not result.allowed and result.findings[0].detector == "jwt"


def test_trust_matrix_and_configurable_email_policy():
    secret = "AKIA" + "A" * 16
    body = {"prompt": [secret, "ABCDE1234F", "person@example.com"]}
    local = DLPScanner().protect(body, "local-model")
    assert local.allowed and local.body == body and len(local.findings) == 3
    cloud = DLPScanner().protect(body, "model-provider")
    assert not cloud.allowed and cloud.body is None
    redacted = DLPScanner(redact_high=True).protect(body, "model-provider")
    assert redacted.body["prompt"] == ["[REDACTED:aws-key]", "[REDACTED:pan]", "person@example.com"]
    strict = DLPScanner(cloud_low="redact").protect("person@example.com", "model-provider")
    assert strict.action == "redact"
    disabled = DLPScanner(low_enabled=False).protect("person@example.com", "approved-site")
    assert disabled.action == "allow" and not disabled.findings
    assert body["prompt"][0] == secret  # Never mutate caller-owned payloads.


def test_json_structure_password_fields_and_numeric_cards():
    scanner = DLPScanner(redact_high=True)
    result = scanner.protect({"pwd": "abc", "nested": [4111111111111111, None, True]}, "model-provider")
    assert result.allowed
    assert result.body == {"pwd": "[REDACTED:password]", "nested": ["[REDACTED:payment-card]", None, True]}
    assert json.loads(json.dumps(result.body)) == result.body


def test_private_key_redacts_whole_block_and_overlaps_once():
    key = "-----BEGIN PRIVATE KEY-----\nperson@example.com\n-----END PRIVATE KEY-----"
    result = DLPScanner(redact_high=True).protect(key, "model-provider")
    assert result.body == "[REDACTED:private-key]" and len(result.findings) == 1


def test_redacted_key_collision_fails_closed():
    result = DLPScanner().protect({"ABCDE1234F": 1, "FGHIJ5678K": 2}, "approved-site")
    assert not result.allowed
