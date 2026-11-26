"""Best-effort masking of secrets before text reaches an assistant or the audit log.

This reduces accidental exposure. It is not a security boundary: anyone who can run
commands can encode a secret so that no pattern matches it.
"""

import re

MARKER = "[REDACTED:"

_PEM_BEGIN = r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"
_PEM_END = r"-----END [A-Z0-9 ]*PRIVATE KEY-----"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private-key", re.compile(rf"{_PEM_BEGIN}.*?(?:{_PEM_END}|\Z)", re.S)),
    # A chunk of output may start in the middle of a key.
    ("private-key", re.compile(rf"\A(?:[A-Za-z0-9+/=]+\r?\n)*[A-Za-z0-9+/=]*\r?\n?{_PEM_END}")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("api-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("bridge-token", re.compile(r"\batb(?:ag|s)_[A-Za-z0-9_\-]{30,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
]

# user:password@host inside URLs
_URL_CREDENTIAL = re.compile(r"(\b[a-z][a-z0-9+.\-]{0,30}://[^/\s:@'\"]+:)([^@\s/'\"]{3,})(@)", re.I)
# KEY=value / KEY: value where the key name says it is secret
_ASSIGNMENT = re.compile(
    # The look-behind and the bounded prefix keep matching linear on very long lines.
    r"(?<![A-Za-z0-9_\-])((?:[A-Za-z0-9_\-]{0,64}(?:password|passwd|secret|token|api[_\-]?key|private[_\-]?key|access[_\-]?key))"
    r"[\"']?\s*(?:=|:)\s*)"
    r"(?:\"([^\"\r\n]{4,})\"|'([^'\r\n]{4,})'|([^\s\"',;$(){}<>\[\]]{6,})(?=[\s,;\"'}&]|\Z))",
    re.I,
)


def redact(text: str, known_secrets: tuple[str, ...] = ()) -> str:
    for secret in known_secrets:
        if len(secret) >= 4:
            text = text.replace(secret, f"{MARKER}stored-credential]")
    for label, pattern in _PATTERNS:
        text = pattern.sub(f"{MARKER}{label}]", text)
    text = _URL_CREDENTIAL.sub(rf"\1{MARKER}url-password]\3", text)

    def mask_assignment(match: re.Match[str]) -> str:
        quote = '"' if match.group(2) is not None else "'" if match.group(3) is not None else ""
        value = match.group(2) or match.group(3) or match.group(4)
        if value.startswith("[REDACTED"):
            return match.group(0)
        return f"{match.group(1)}{quote}{MARKER}value]{quote}"

    return _ASSIGNMENT.sub(mask_assignment, text)


def redact_value(value, known_secrets: tuple[str, ...] = ()):
    """Apply redact() to every string inside a JSON-like structure."""
    if isinstance(value, str):
        return redact(value, known_secrets)
    if isinstance(value, dict):
        return {key: redact_value(item, known_secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item, known_secrets) for item in value]
    return value
