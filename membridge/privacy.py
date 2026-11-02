import re

PATTERNS = [
    (
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b"
        ),
        "[REDACTED TOKEN]",
    ),
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|secret|authorization)\b[\"']?\s*[:=]\s*)(?:[\"'][^\"'\r\n]+[\"']|[^\s,;]+)"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/]+:)[^\s@/]+@"), r"\1[REDACTED]@"),
]


def redact_text(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.fullmatch(r"(?i)(api[_-]?key|access[_-]?token|password|secret|authorization)", k)
            else redact(v)
            for k, v in value.items()
        }
    return value
