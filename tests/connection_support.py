"""Explicit managed connections for integration and unit tests."""

import json
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

from auth_support import OperatorClient
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.core.encryption import ConnectionCipher
from app.integrations.outbound import OutboundHTTP, OutboundPolicy
from app.models.auth import LEGACY_WORKSPACE_ID
from app.repositories.connections import (
    ConnectionRepository,
    ConnectionSnapshot,
    ensure_key_check,
    legacy_connection_id,
)

INSTANCE_ID = legacy_connection_id(LEGACY_WORKSPACE_ID, "n8n")
KEYRING = json.dumps({"test": Fernet.generate_key().decode()})
CIPHER = ConnectionCipher(KEYRING, "test")


def snapshot(**values):
    base = ConnectionSnapshot(
        workspace_id=LEGACY_WORKSPACE_ID,
        id=INSTANCE_ID,
        revision=1,
        kind="n8n",
        name="Test instance",
        base_url="https://8.8.8.8/api/v1",
        model=None,
        api_key=SecretStr("key"),
    )
    return replace(base, **values)


def outbound(http, base_url="https://8.8.8.8/api/v1"):
    parsed = urlsplit(base_url)
    config = SimpleNamespace(
        deployment_environment="development",
        outbound_allow_local_http=False,
        outbound_egress_enforced=False,
        outbound_destinations=json.dumps([{"origin": f"{parsed.scheme}://{parsed.netloc}"}]),
    )
    return OutboundHTTP(http, OutboundPolicy(config))


def provision(factory, *, workspace_id=LEGACY_WORKSPACE_ID, kind="n8n", reuse=True, **values):
    if hasattr(factory, "workspace_id"):
        workspace_id = factory.workspace_id
        factory = sessionmaker(factory._factory.kw["bind"], expire_on_commit=False)
    with factory() as db:
        repo = ConnectionRepository(db, workspace_id, CIPHER)
        existing = repo.enabled(kind)
        if existing and reuse:
            return repo.snapshot(kind, existing[0].id)
        ensure_key_check(db, CIPHER)
        data = {"name": "Test instance", "base_url": "https://8.8.8.8/api/v1", "api_key": "key"}
        if kind == "ai":
            data.update(kind="openai-compatible", model="test-model")
        data.update(values)
        row = repo.create(kind, data)
        row.enabled = True
        db.flush()
        if kind == "ai":
            repo.state().default_ai_provider_id = row.id
        db.commit()
        return repo.snapshot(kind, row.id)


class ManagedClient(OperatorClient):
    """Provision DB records from explicit test data before exercising the runtime."""

    def __init__(self, app, **kwargs):
        config = app.state.settings
        if not config.demo_mode:
            config.deployment_environment = "development"
            config.connection_encryption_keys = SecretStr(KEYRING)
            config.connection_active_key_id = "test"
            urls = [config.n8n_base_url, config.ai_base_url]
            config.outbound_destinations = json.dumps(
                [
                    {"origin": f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"}
                    for url in dict.fromkeys(urls)
                    if url
                ]
            )
        super().__init__(app, **kwargs)

    def __enter__(self):
        super().__enter__()
        config = self.app.state.settings
        if not config.demo_mode and config.n8n_base_url:
            snap = provision(
                self.app.state.session_factory,
                base_url=config.n8n_base_url,
                api_key=config.n8n_api_key.get_secret_value(),
            )
            self.instance_id = snap.id
            self.portal.call(self.app.state.registry.reconcile)
        if not config.demo_mode and config.ai_api_key.get_secret_value():
            provision(
                self.app.state.session_factory,
                kind="ai",
                base_url=config.ai_base_url,
                model=config.ai_model,
                api_key=config.ai_api_key.get_secret_value(),
            )
        return self
