import re


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
    return text.split("\n")[0][:1000]
