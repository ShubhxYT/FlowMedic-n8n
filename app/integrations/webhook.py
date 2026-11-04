"""Fail-closed validation for outbound alert webhook destinations."""

from ipaddress import ip_address
from urllib.parse import urlsplit


class WebhookConfigurationError(ValueError):
    """Raised when a webhook destination does not meet the outbound safety policy."""


def validate_webhook_url(value: str) -> str:
    """Allow only direct HTTPS requests to a globally routable IP address.

    DNS names are intentionally rejected. HTTPX does not offer destination pinning for
    a pre-resolved hostname, so accepting names would leave a DNS-rebinding window.
    This conservative policy fails closed until a pinned resolver/transport is added.
    """
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise WebhookConfigurationError(
            "Webhook URL must be HTTPS without credentials, query parameters, or fragments"
        )
    try:
        address = ip_address(parsed.hostname)
    except ValueError as exc:
        raise WebhookConfigurationError(
            "Webhook hostnames are not supported until destination-pinned DNS is available"
        ) from exc
    if not address.is_global:
        raise WebhookConfigurationError(
            "Webhook URL must use a globally routable address; "
            "local and private destinations are blocked"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise WebhookConfigurationError("Webhook URL has an invalid port") from exc
    return value
