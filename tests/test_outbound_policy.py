import asyncio
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from app.core.errors import ServiceError
from app.integrations.outbound import OutboundHTTP, OutboundPolicy, canonical_base


def config(**overrides):
    values = {
        "deployment_environment": "production",
        "outbound_allow_local_http": False,
        "outbound_egress_enforced": True,
        "outbound_destinations": json.dumps(
            [{"origin": "https://n8n.example.test"}, {"origin": "https://api.example.test"}]
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def public_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private_dns(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", port))]


def mapped_dns(host, port, *args, **kwargs):
    return [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::ffff:127.0.0.1", port, 0, 0))
    ]


@pytest.mark.parametrize(
    "value",
    [
        "http://n8n.example.test",
        "https://user:pass@n8n.example.test",
        "https://n8n.example.test?x=1",
        "https://n8n.example.test#fragment",
        "https://n8n.example.test/../escape",
        "https://n8n.example.test\\evil",
        "ftp://n8n.example.test",
    ],
)
def test_canonical_base_rejects_unsafe_urls(value):
    with pytest.raises(ServiceError):
        canonical_base(value, allow_http=False)


def test_policy_requires_egress_acknowledgement_and_allowlist(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    policy = OutboundPolicy(config(outbound_egress_enforced=False))
    with pytest.raises(ServiceError) as error:
        asyncio.run(policy.validate("https://n8n.example.test"))
    assert error.value.code == "outbound_egress_unconfigured"

    policy = OutboundPolicy(config())
    with pytest.raises(ServiceError) as error:
        asyncio.run(policy.validate("https://other.example.test"))
    assert error.value.code == "outbound_destination_forbidden"


def test_policy_rejects_private_link_local_and_mapped_addresses(monkeypatch):
    policy = OutboundPolicy(config())
    for resolver in (private_dns, mapped_dns):
        monkeypatch.setattr(socket, "getaddrinfo", resolver)
        with pytest.raises(ServiceError) as error:
            asyncio.run(policy.validate("https://n8n.example.test"))
        assert error.value.code == "outbound_destination_forbidden"

    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    assert (
        asyncio.run(policy.validate("https://n8n.example.test"))
        == "https://n8n.example.test"
    )


def test_invalid_operator_policy_fails_closed():
    for value in ("not json", "{}", '[{"origin":"https://a.test","extra":1}]'):
        with pytest.raises(ServiceError) as error:
            OutboundPolicy(config(outbound_destinations=value))
        assert error.value.code == "outbound_policy_invalid"


def run_with_transport(handler, call):
    async def runner():
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, trust_env=False)
        policy = OutboundPolicy(config())
        http = OutboundHTTP(client, policy)
        return await call(http)

    return asyncio.run(runner())


def test_redirect_rejected_and_large_response_bounded(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.example"})

    with pytest.raises(ServiceError) as error:
        run_with_transport(
            redirect, lambda http: http.get("https://n8n.example.test", "/x")
        )
    assert error.value.code == "upstream_redirect_rejected"

    def huge(request):
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ServiceError) as error:
        run_with_transport(huge, lambda http: http.get("https://n8n.example.test", "/x"))
    assert error.value.code == "upstream_response_too_large"


def test_pooled_client_never_replays_cookies_across_workspaces(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)
    seen = []

    def handler(request):
        seen.append(request.headers.get("cookie"))
        response = httpx.Response(200, json={"ok": True})
        response.headers["set-cookie"] = "leak=workspace-a; Path=/"
        return response

    async def runner():
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, trust_env=False)
        http = OutboundHTTP(client, OutboundPolicy(config()))
        await http.get(
            "https://n8n.example.test", "/a", headers={"X-N8N-API-KEY": "key-a"}
        )
        await http.get(
            "https://n8n.example.test", "/b", headers={"X-N8N-API-KEY": "key-b"}
        )
        await client.aclose()

    asyncio.run(runner())
    assert seen == [None, None]


def test_timeout_maps_to_safe_error(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", public_dns)

    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ServiceError) as error:
        run_with_transport(slow, lambda http: http.get("https://n8n.example.test", "/x"))
    assert error.value.code == "upstream_timeout"
