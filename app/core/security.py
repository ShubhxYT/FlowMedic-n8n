import re

# Bare token literals that carry no "key=" prefix. Heuristic, not exhaustive.
_TOKEN_PATTERNS = (
    # sk-live-..., sk-proj-...
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    # GitHub tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    # GitHub fine-grained PAT
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    # Slack tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    # AWS access key id
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # JWT
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]*"),
    # long hex (md5/sha/keys)
    re.compile(r"\b[a-fA-F0-9]{32,}\b"),
    # 32+ char base64-ish blob; must mix letters and digits to avoid plain words/paths
    re.compile(
        r"(?<![\w/.])(?=[A-Za-z0-9+/_-]*\d)(?=[A-Za-z0-9+/_-]*[A-Za-z])"
        r"[A-Za-z0-9+/_-]{32,}={0,2}(?![\w/.])"
    ),
)


def sanitize(value: object, secrets: tuple[str, ...] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"https?://\S+", "[URL REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"""(?ix)(["']?(?:api[_-]?key|password|secret|token|authorization|cookie)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)""",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[EMAIL REDACTED]", text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub("[TOKEN REDACTED]", text)
    return text.split("\n")[0][:1000]
