"""Small, deterministic outbound DLP. Findings never retain matched values."""

import base64
from dataclasses import dataclass, field
import json
import re
from typing import Any


def luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isascii() and c.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2:
            digit *= 2
            digit = digit - 9 if digit > 9 else digit
        total += digit
    return total % 10 == 0


_D = (
    (0,1,2,3,4,5,6,7,8,9), (1,2,3,4,0,6,7,8,9,5),
    (2,3,4,0,1,7,8,9,5,6), (3,4,0,1,2,8,9,5,6,7),
    (4,0,1,2,3,9,5,6,7,8), (5,9,8,7,6,0,4,3,2,1),
    (6,5,9,8,7,1,0,4,3,2), (7,6,5,9,8,2,1,0,4,3),
    (8,7,6,5,9,3,2,1,0,4), (9,8,7,6,5,4,3,2,1,0),
)
_P = (
    (0,1,2,3,4,5,6,7,8,9), (1,5,7,6,2,8,3,0,9,4),
    (5,8,0,3,7,9,6,1,4,2), (8,9,1,6,0,4,3,5,2,7),
    (9,4,5,3,1,2,6,8,7,0), (4,2,8,6,5,7,3,9,0,1),
    (2,7,9,3,8,0,6,4,1,5), (7,0,4,6,9,1,3,2,5,8),
)


def verhoeff(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    if not re.fullmatch(r"[2-9][0-9]{11}", digits):
        return False
    check = 0
    for index, digit in enumerate(reversed(digits)):
        check = _D[check][_P[index % 8][int(digit)]]
    return check == 0


def is_jwt(value: str) -> bool:
    try:
        parts = value.split(".")
        if len(parts) != 3 or not parts[2]:
            return False
        # JWT header and payload are JSON; the signature is binary, not JSON.
        return all(isinstance(json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4))), dict)
                   for p in parts[:2])
    except (ValueError, UnicodeError):
        return False


@dataclass(frozen=True)
class Finding:
    detector: str
    severity: str
    start: int
    end: int


@dataclass(frozen=True)
class DLPResult:
    allowed: bool
    action: str
    body: Any = field(repr=False)
    findings: tuple[Finding, ...] = ()

    def summary(self) -> dict:
        return {"action": self.action, "findings": len(self.findings), "detectors": [
            {"id": f.detector, "severity": f.severity, "start": f.start, "end": f.end}
            for f in self.findings
        ]}


# Length bounds intentionally cover common token families, not every credential.
_PATTERNS = (
    ("private-key", "high", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*?(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|$)", None),
    ("openai-key", "high", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b", None),
    ("google-key", "high", r"\bAIza[A-Za-z0-9_-]{35}\b", None),
    ("github-key", "high", r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,})\b", None),
    ("aws-key", "high", r"\bAKIA[A-Z0-9]{16}\b", None),
    ("slack-key", "high", r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b", None),
    ("jwt", "high", r"(?<![\w-])[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![\w-])", is_jwt),
    ("password", "high", r"(?i)[\"']?\b(?:password|passwd|pwd)[\"']?\s*[:=]\s*(?:\"[^\"]+\"|'[^']+'|[^\s,;}]+)", None),
    ("payment-card", "high", r"(?<![0-9])[0-9](?:[ -]?[0-9]){12,18}(?![0-9])", luhn),
    ("aadhaar", "high", r"(?<![0-9])[2-9][0-9]{3}[ -]?[0-9]{4}[ -]?[0-9]{4}(?![0-9])", verhoeff),
    ("pan", "medium", r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", None),
    ("email", "low", r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b", None),
    ("phone", "low", r"(?<![\w])(?:\+[1-9][0-9]{0,2}[ -]?)?(?:\([0-9]{3}\)|[0-9]{3})[ -][0-9]{3}[ -][0-9]{4}(?![0-9])", None),
)


class DLPScanner:
    def __init__(self, *, redact_high: bool = False, cloud_low: str = "allow", low_enabled: bool = True):
        if cloud_low not in {"allow", "redact"}:
            raise ValueError("cloud_low must be allow or redact")
        self.redact_high = redact_high
        self.cloud_low = cloud_low
        self.low_enabled = low_enabled

    def scan(self, text: str) -> tuple[Finding, ...]:
        findings = []
        for detector, severity, pattern, validator in _PATTERNS:
            if severity == "low" and not self.low_enabled:
                continue
            for match in re.finditer(pattern, text, re.DOTALL):
                if validator is None or validator(match.group()):
                    findings.append(Finding(detector, severity, match.start(), match.end()))
        # Prefer higher severity for overlapping candidates (card versus phone).
        ordered = sorted(findings, key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f.severity], f.start, -f.end))
        selected = []
        for finding in ordered:
            if not any(finding.start < other.end and other.start < finding.end for other in selected):
                selected.append(finding)
        return tuple(sorted(selected, key=lambda f: f.start))

    def protect(self, body: Any, category: str) -> DLPResult:
        """Inspect JSON recursively without corrupting JSON syntax when redacting.

        Positions refer to individual scalar fields; no field names or values
        are retained in findings, because even JSON keys may contain secrets.
        """
        findings: list[Finding] = []
        blocked = False
        redacted = False

        def clean_text(text, password=False):
            nonlocal blocked, redacted
            matches = (Finding("password", "high", 0, len(text)),) if password and text else self.scan(text)
            findings.extend(matches)
            if category == "local-model":
                return text
            output = text
            for match in reversed(matches):
                if match.severity == "high" and not self.redact_high:
                    blocked = True
                elif match.severity != "low" or category != "model-provider" or self.cloud_low == "redact":
                    output = output[:match.start] + f"[REDACTED:{match.detector}]" + output[match.end:]
                    redacted = True
            return output

        def walk(value, password=False):
            nonlocal blocked
            if isinstance(value, dict):
                result = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError("JSON keys must be strings")
                    safe_key = clean_text(key)
                    if safe_key in result:
                        blocked = True
                    result[safe_key] = walk(item, key.lower() in {"password", "passwd", "pwd"})
                return result
            if isinstance(value, list):
                return [walk(item, password) for item in value]
            if isinstance(value, str):
                return clean_text(value, password)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                text = str(value)
                cleaned = clean_text(text, password)
                return value if text == cleaned else cleaned
            if value is None or isinstance(value, bool):
                return value
            raise ValueError("Unsupported JSON value")

        safe = walk(body)
        return DLPResult(not blocked, "block" if blocked else "redact" if redacted else "allow",
                         None if blocked else safe, tuple(findings))
