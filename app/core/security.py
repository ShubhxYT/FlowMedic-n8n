import math
import re
from collections import Counter

from starlette.responses import JSONResponse

_REDACTED = "[REDACTED]"
_TOKEN_REDACTED = "[TOKEN REDACTED]"

# Bare token literals that carry no "key=" prefix. This is heuristic, not exhaustive.
_TOKEN_PATTERNS = (
    # OpenAI-style keys: sk-..., sk-live-..., sk-proj-..., sk-test-...
    re.compile(
        r"(?<![A-Za-z0-9])sk-(?:live-|proj-|test-)?[A-Za-z0-9_+/=-]{8,}"
        r"(?![A-Za-z0-9_+/=-])"
    ),
    # GitHub classic and fine-grained personal access tokens.
    re.compile(
        r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
        r"(?![A-Za-z0-9_])"
    ),
    # Slack tokens.
    re.compile(r"(?<![A-Za-z0-9-])xox[abprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9-])"),
    # AWS access key IDs.
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    # JWTs: three dot-separated Base64URL segments beginning with the eyJ header.
    re.compile(
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
        r"(?![A-Za-z0-9_-])"
    ),
    # Long hexadecimal digests and keys; require a letter so numeric IDs are preserved.
    re.compile(
        r"(?<![A-Za-z0-9])"
        r"(?=[a-fA-F0-9]{32,}(?![A-Za-z0-9]))"
        r"(?=[a-fA-F0-9]*[a-fA-F])"
        r"[a-fA-F0-9]{32,}"
        r"(?![A-Za-z0-9])"
    ),
)

# Conservative fallback for unknown Base64/Base64URL-like secrets. The callback below
# rejects ordinary identifiers, UUIDs, hex-only values, and low-entropy text.
_BASE64_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9+/_-])([A-Za-z0-9+/_-]{32,}={0,2})(?![A-Za-z0-9+/_-])"
)
_UUID_FULL = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_HEX_FULL = re.compile(r"[a-fA-F0-9]{32,}")


def _entropy(text: str) -> float:
    counts = Counter(text)
    length = len(text)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _redact_high_entropy(match: re.Match[str]) -> str:
    candidate = match.group(1)
    core = candidate.rstrip("=")
    if _UUID_FULL.fullmatch(core) or _HEX_FULL.fullmatch(core):
        return candidate
    if not any(char.isdigit() for char in core) or not any(char.isalpha() for char in core):
        return candidate
    if len(set(core)) < 12 or _entropy(core) < 4.2:
        return candidate
    return _TOKEN_REDACTED


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before JSON parsing."""

    def __init__(self, app, max_bytes: int = 32 * 1024):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {key.lower(): value for key, value in scope["headers"]}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    return await self._reject(scope, receive, send)
            except ValueError:
                return await self._reject(scope, receive, send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope, receive, send):
        response = JSONResponse(
            {"error": {"code": "payload_too_large", "message": "Request body is too large"}},
            status_code=413,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)


class _BodyTooLarge(Exception):
    pass


def sanitize(value: object, secrets: tuple[str, ...] = ()) -> str:
    # Bound regex work on unbounded upstream error bodies. Output is capped at 1000
    # characters, so a wider window adds no value beyond protecting a boundary token.
    text = str(value).split("\n")[0][:4096]
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED)
    text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
    text = re.sub(
        r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"""(?ix)(["']?(?:api[_-]?key|password|secret|token|authorization|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL REDACTED]", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(_TOKEN_REDACTED, text)
    text = _BASE64_CANDIDATE.sub(_redact_high_entropy, text)
    return text[:1000]
