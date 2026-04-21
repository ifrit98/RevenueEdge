"""PII redaction for log output and outbound payloads.

Ported verbatim from SMB-MetaPattern/apps/api-gateway/app/pii_filter.py. Covers
emails, phone numbers, SSN, credit card, common API-key formats, AWS/Google/
Slack/Stripe/GitHub tokens, JWTs, DOB, US street addresses.
"""

from __future__ import annotations

import logging
import re

_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?:\+1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}",
)
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_PATTERN = re.compile(r"\b(?:\d[-\s]?){12,18}\d\b")
_API_KEY_PATTERN = re.compile(
    r"\b(?:sk-[a-zA-Z0-9]{20,}|key_[a-zA-Z0-9]{20,}|token_[a-zA-Z0-9]{20,})\b",
    re.IGNORECASE,
)
_AWS_KEY_PATTERN = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GOOGLE_KEY_PATTERN = re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")
_SLACK_TOKEN_PATTERN = re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")
_STRIPE_KEY_PATTERN = re.compile(r"\b(?:sk_live|rk_live)_[0-9A-Za-z]{16,}\b")
_GITHUB_TOKEN_PATTERN = re.compile(r"\bgh[pousr]_[0-9A-Za-z]{20,}\b")
_JWT_PATTERN = re.compile(
    r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_DOB_PATTERN = re.compile(
    r"\b("
    r"(?:0?[1-9]|1[0-2])[-/](?:0?[1-9]|[12]\d|3[01])[-/](?:19|20)\d{2}"
    r"|"
    r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r")\b"
)
_STREET_PATTERN = re.compile(
    r"\b\d{1,5}\s+(?:[A-Za-z0-9'.\-]+\s+){1,4}"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|"
    r"Place|Pl|Way|Parkway|Pkwy|Highway|Hwy|Terrace|Ter|Circle|Cir|Square|Sq|"
    r"Trail|Trl)\b\.?",
    re.IGNORECASE,
)

_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_AWS_KEY_PATTERN, "[AWS_KEY_REDACTED]"),
    (_GOOGLE_KEY_PATTERN, "[GOOGLE_KEY_REDACTED]"),
    (_SLACK_TOKEN_PATTERN, "[SLACK_TOKEN_REDACTED]"),
    (_STRIPE_KEY_PATTERN, "[STRIPE_KEY_REDACTED]"),
    (_GITHUB_TOKEN_PATTERN, "[GITHUB_TOKEN_REDACTED]"),
    (_JWT_PATTERN, "[JWT_REDACTED]"),
    (_API_KEY_PATTERN, "[API_KEY_REDACTED]"),
    (_EMAIL_PATTERN, "[EMAIL_REDACTED]"),
    (_SSN_PATTERN, "[SSN_REDACTED]"),
    (_CC_PATTERN, "[CC_REDACTED]"),
    (_DOB_PATTERN, "[DOB_REDACTED]"),
    (_STREET_PATTERN, "[STREET_REDACTED]"),
    (_PHONE_PATTERN, "[PHONE_REDACTED]"),
]


def redact_pii(text: str) -> str:
    if not text or not isinstance(text, str):
        return text
    result = text
    for pattern, replacement in _PII_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_structure(value):
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {k: redact_structure(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_structure(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_structure(v) for v in value)
    return value


class PiiRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact_pii(message)
            record.msg = redacted
            record.args = ()
        except Exception:
            pass
        return True
