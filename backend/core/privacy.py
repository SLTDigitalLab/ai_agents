"""Shared masking for chat input and routing evidence."""
import re

PII_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[EMAIL]",
    ),
    # Sri Lankan mobile numbers: +947XXXXXXXX / 947XXXXXXXX / 07XXXXXXXX
    (
        re.compile(r"\b(?:\+?94|0)?7\d{8}\b"),
        "[PHONE]",
    ),
    # Sri Lankan NIC: old 123456789V / new 12 digits
    (
        re.compile(r"\b(?:\d{9}[VXvx]|\d{12})\b"),
        "[NIC]",
    ),
    # Credit/debit-card-like long numbers
    (
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "[CARD_NUMBER]",
    ),
    # Common API keys / JWT / bearer-token-like secrets
    (
        re.compile(
            r"(?i)\b(?:bearer\s+)?("
            r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"
            r"|sk-[a-zA-Z0-9_-]{20,}"
            r"|AIza[0-9A-Za-z_-]{20,}"
            r")\b"
        ),
        "[SECRET]",
    ),
]


def mask_pii(text: str) -> str:
    """Mask common PII/secrets before sending text deeper into the agent."""
    masked = text or ""

    for pattern, replacement in PII_PATTERNS:
        masked = pattern.sub(replacement, masked)

    return masked

