import re
from typing import Literal

Severity = Literal["high", "medium", "low"]

_STATUS_RE = re.compile(r"(?i)\b(?:status(?:\s+code)?|http(?:\s+code)?)\D{0,3}([1-5]\d{2})\b")
_AUTH_RE = re.compile(r"(?i)\b(?:unauthori[sz]ed|forbidden|authori[sz]ation failed|invalid credentials)\b")
_TIMEOUT_RE = re.compile(r"(?i)\b(?:ETIMEDOUT|ESOCKETTIMEDOUT|timed?[\s-]?out|timeout)\b")
_NETWORK_RE = re.compile(
    r"\b(?:ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|EHOSTUNREACH|ENETUNREACH)\b"
    r"|(?i:getaddrinfo|dns lookup|could not resolve)"
)


def _http_code(error: dict, message: str) -> int | None:
    for key in ("httpCode", "statusCode"):
        try:
            return int(error[key])
        except (KeyError, TypeError, ValueError):
            pass
    match = _STATUS_RE.search(message)
    return int(match.group(1)) if match else None


def classify_severity(status: str | None, error: dict) -> Severity:
    """Heuristic severity from execution status and the (unsanitized) n8n error."""
    if status == "crashed":
        return "high"
    message = str(error.get("message") or "")
    description = str(error.get("description") or "")
    text = f"{message} {description}"
    code = _http_code(error, text)
    if code in (401, 403) or (code is not None and 500 <= code <= 599):
        return "high"
    if _AUTH_RE.search(text):
        return "high"
    if _TIMEOUT_RE.search(text):  # timeouts stay medium, checked before connect errors
        return "medium"
    if _NETWORK_RE.search(text):
        return "high"
    return "medium"