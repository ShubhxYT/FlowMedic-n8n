import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.errors import ServiceError


def denied() -> ServiceError:
    return ServiceError("outbound_destination_forbidden", "Destination is not permitted", 422)


def canonical_base(value: str, *, allow_http: bool) -> str:
    try:
        parsed = urlsplit(value)
        if (
            len(value) > 2048
            or any(character.isspace() or ord(character) < 32 for character in value)
            or "\\" in value
            or parsed.scheme not in ({"https", "http"} if allow_http else {"https"})
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
        ):
            raise ValueError
        hostname = parsed.hostname.rstrip(".").encode("idna").decode().lower()
        if "%" in hostname:
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        host = f"[{hostname}]" if ":" in hostname else hostname
        default = 443 if parsed.scheme == "https" else 80
        authority = host if port == default else f"{host}:{port}"
        path = parsed.path.rstrip("/")
        if any(segment in {".", ".."} for segment in path.split("/")):
            raise ValueError
        if "%" in path:
            raise ValueError
        return urlunsplit((parsed.scheme, authority, path, "", ""))
    except (TypeError, ValueError, UnicodeError):
        raise denied() from None


@dataclass(frozen=True, slots=True)
class Destination:
    origin: tuple[str, str, int]
    allow_private: bool


class OutboundPolicy:
    def __init__(self, config):
        self.allow_http = bool(
            config.deployment_environment == "development" and config.outbound_allow_local_http
        )
        self.production = config.deployment_environment == "production"
        self.egress_enforced = config.outbound_egress_enforced
        self.destinations: dict[tuple[str, str, int], Destination] = {}
        try:
            entries = json.loads(config.outbound_destinations)
            if not isinstance(entries, list):
                raise ValueError
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) - {"origin", "allow_private"}:
                    raise ValueError
                if not isinstance(entry.get("allow_private", False), bool):
                    raise ValueError
                base = canonical_base(entry["origin"], allow_http=self.allow_http)
                parsed = urlsplit(base)
                if parsed.path:
                    raise ValueError
                key = self.origin(base)
                if key in self.destinations:
                    raise ValueError
                self.destinations[key] = Destination(key, entry.get("allow_private", False))
        except (KeyError, TypeError, ValueError, ServiceError):
            raise ServiceError(
                "outbound_policy_invalid", "Operator outbound policy is invalid", 503
            ) from None

    @staticmethod
    def origin(base: str) -> tuple[str, str, int]:
        parsed = urlsplit(base)
        return (
            parsed.scheme,
            parsed.hostname or "",
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )

    async def validate(self, value: str) -> str:
        base = canonical_base(value, allow_http=self.allow_http)
        scheme, host, port = self.origin(base)
        approved = self.destinations.get((scheme, host, port))
        if approved is None:
            raise denied()
        if self.production and not self.egress_enforced:
            raise ServiceError(
                "outbound_egress_unconfigured", "Operator egress controls are required", 503
            )
        try:
            async with asyncio.timeout(3):
                addresses = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
        except (OSError, TimeoutError):
            raise ServiceError(
                "outbound_dns_failed", "Destination could not be resolved", 503
            ) from None
        if not addresses:
            raise denied()
        for answer in addresses:
            address = ipaddress.ip_address(answer[4][0])
            address = getattr(address, "ipv4_mapped", None) or address
            # Link-local includes common cloud metadata IPs. This PR does not allow it.
            if address.is_link_local or address.is_multicast or address.is_unspecified:
                raise denied()
            if not address.is_global and not approved.allow_private:
                raise denied()
            if not address.is_global and not (address.is_private or address.is_loopback):
                raise denied()
        return base


class OutboundHTTP:
    """Stateless requests over a pooled HTTPX client; no automatic cookie/header merging."""

    def __init__(self, client: httpx.AsyncClient, policy: OutboundPolicy, timeout: float = 15):
        self.client = client
        self.policy = policy
        self.timeout = min(timeout, 30)

    async def request(
        self, method: str, base_url: str, path: str, *, headers=None, guard=None, **kwargs
    ):
        try:
            async with asyncio.timeout(self.timeout):
                base = await self.policy.validate(base_url)
                if not path.startswith("/") or path.startswith("//") or "://" in path:
                    raise denied()
                if guard is not None:
                    guard()
                request = httpx.Request(
                    method,
                    base + path,
                    headers={"Accept": "application/json", **(headers or {})},
                    extensions={"timeout": httpx.Timeout(self.timeout).as_dict()},
                    **kwargs,
                )
                # Constructing Request directly bypasses AsyncClient's persistent cookies.
                response = await self.client.send(
                    request, auth=None, follow_redirects=False, stream=True
                )
                try:
                    if 300 <= response.status_code < 400:
                        raise ServiceError(
                            "upstream_redirect_rejected",
                            "Destination redirected the request",
                            502,
                        )
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 2 * 1024 * 1024:
                            raise ServiceError(
                                "upstream_response_too_large",
                                "Upstream response is too large",
                                502,
                            )
                    return httpx.Response(
                        response.status_code,
                        headers={"Content-Type": response.headers.get("content-type", "")},
                        content=bytes(data),
                        request=request,
                    )
                finally:
                    await response.aclose()
        except (TimeoutError, httpx.TimeoutException):
            raise ServiceError("upstream_timeout", "Upstream request timed out", 504) from None
        except httpx.RequestError:
            raise ServiceError(
                "upstream_unreachable", "Upstream service is unavailable", 503
            ) from None

    async def get(self, base_url: str, path: str, **kwargs):
        return await self.request("GET", base_url, path, **kwargs)

    async def post(self, base_url: str, path: str, **kwargs):
        return await self.request("POST", base_url, path, **kwargs)
