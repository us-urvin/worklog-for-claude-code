"""Best-effort redaction of secrets and PII before anything leaves the machine.

This is a safety net, not a guarantee. The data repo must still be PRIVATE.
Order matters: credentials-in-URLs must be handled before e-mail addresses.
"""
from __future__ import annotations

import re

_R = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     "[REDACTED_PRIVATE_KEY]"),
    # scheme://user:password@host  ->  scheme://[REDACTED]@host
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1" + _R + "@"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), _R),               # Anthropic
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), _R),                   # OpenAI-style
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), _R),  # GitHub
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), _R),
    (re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"), _R),                # GitLab
    (re.compile(r"\bATBB[A-Za-z0-9]{20,}"), _R),                    # Bitbucket app password
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _R),                      # AWS access key id
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), _R),           # Slack
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), _R),                 # Google API key
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED_JWT]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer " + _R),
    # key=value / key: value for obviously sensitive keys
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)[A-Za-z0-9_]*)"
                r"(\s*[:=]\s*)(['\"]?)[^\s'\"]{4,}\3"), r"\1\2" + _R),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"), "[REDACTED_IP]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
