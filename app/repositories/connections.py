"""Workspace-scoped persistence for encrypted n8n and AI connections."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import SecretStr
from sqlalchemy import func, select, update

from app.core.errors import ServiceError
from app.models.connections import (
    AIProvider,
    ConnectionKeyCheck,
    N8nInstance,
    WorkspaceConnectionState,
)

IMPORT_TAG = "legacy-env-v1"
MARKER_ID = "settings"
KEY_CHECK_SECRET = "flowmedic-connections-v1"
MARKER_WORKSPACE = "system"
MARKER_CONNECTION = "key-check"
MODELS = {"n8n": N8nInstance, "ai": AIProvider}


def utc_now() -> datetime:
    return datetime.now(UTC)


def not_found() -> ServiceError:
    return ServiceError("connection_not_found", "Connection was not found", 404)


def conflict() -> ServiceError:
    return ServiceError("connection_changed", "Connection changed; reload and retry", 409)


def connection_limit() -> ServiceError:
    return ServiceError("connection_limit_reached", "Connection limit reached", 409)


def legacy_connection_id(workspace_id: str, kind: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"flowmedic:{workspace_id}:legacy:{kind}"))


@dataclass(frozen=True, slots=True)
class ConnectionSnapshot:
    workspace_id: str
    id: str
    revision: int
    kind: Literal["n8n", "openai-compatible", "mock"]
    name: str
    base_url: str | None
    model: str | None
    api_key: SecretStr = field(repr=False, compare=False)


class ConnectionRepository:
    def __init__(self, session, workspace_id: str, cipher):
        self.session = session
        self.workspace_id = workspace_id
        self.cipher = cipher

    def _model(self, kind: str):
        try:
            return MODELS[kind]
        except KeyError:
            raise ValueError("Unknown connection kind") from None

    def list(self, kind: str) -> list:
        model = self._model(kind)
        return list(
            self.session.scalars(
                select(model)
                .where(model.workspace_id == self.workspace_id, model.deleted.is_(False))
                .order_by(model.created_at, model.id)
                .limit(100)
            )
        )

    def enabled(self, kind: str) -> list:
        model = self._model(kind)
        return list(
            self.session.scalars(
                select(model).where(
                    model.workspace_id == self.workspace_id,
                    model.deleted.is_(False),
                    model.enabled.is_(True),
                )
            )
        )

    def get(self, kind: str, connection_id: str, *, include_deleted: bool = False):
        model = self._model(kind)
        statement = select(model).where(
            model.workspace_id == self.workspace_id,
            model.id == connection_id,
        )
        if not include_deleted:
            statement = statement.where(model.deleted.is_(False))
        row = self.session.scalar(statement.execution_options(populate_existing=True))
        if row is None:
            raise not_found()
        return row

    def legacy(self, kind: str):
        model = self._model(kind)
        return self.session.scalar(
            select(model).where(
                model.workspace_id == self.workspace_id,
                model.import_tag == IMPORT_TAG,
            )
        )

    def live_count(self, kind: str) -> int:
        model = self._model(kind)
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == self.workspace_id, model.deleted.is_(False))
            )
            or 0
        )

    def create(self, kind: str, values: dict):
        model = self._model(kind)
        if self.live_count(kind) >= 100:
            raise connection_limit()
        connection_id = str(uuid.uuid4())
        api_key = values.pop("api_key", None)
        ciphertext = (
            self.cipher.encrypt(
                api_key, workspace_id=self.workspace_id, connection_id=connection_id
            )
            if api_key
            else None
        )
        now = utc_now()
        row = model(
            id=connection_id,
            workspace_id=self.workspace_id,
            api_key_ciphertext=ciphertext,
            enabled=False,
            deleted=False,
            revision=1,
            created_at=now,
            updated_at=now,
            **values,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def update(self, kind: str, connection_id: str, expected_revision: int, values: dict):
        model = self._model(kind)
        row = self.get(kind, connection_id)
        if row.api_key_ciphertext:
            # A wrong deployment keyring must never permit replacement or removal.
            self.cipher.decrypt(
                row.api_key_ciphertext,
                workspace_id=self.workspace_id,
                connection_id=connection_id,
            )
        api_key = values.pop("api_key", None)
        if values.pop("remove_api_key", False):
            values["api_key_ciphertext"] = None
        elif api_key:
            values["api_key_ciphertext"] = self.cipher.encrypt(
                api_key, workspace_id=self.workspace_id, connection_id=connection_id
            )
        values["updated_at"] = utc_now()
        values["revision"] = model.revision + 1
        result = self.session.execute(
            update(model)
            .where(
                model.workspace_id == self.workspace_id,
                model.id == connection_id,
                model.deleted.is_(False),
                model.revision == expected_revision,
            )
            .values(**values),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            raise conflict()
        return self.get(kind, connection_id)

    def soft_delete(self, kind: str, connection_id: str, expected_revision: int) -> None:
        model = self._model(kind)
        row = self.get(kind, connection_id)
        if row.api_key_ciphertext:
            self.cipher.decrypt(
                row.api_key_ciphertext,
                workspace_id=self.workspace_id,
                connection_id=connection_id,
            )
        result = self.session.execute(
            update(model)
            .where(
                model.workspace_id == self.workspace_id,
                model.id == connection_id,
                model.deleted.is_(False),
                model.revision == expected_revision,
            )
            .values(
                deleted=True,
                enabled=False,
                api_key_ciphertext=None,
                revision=model.revision + 1,
                updated_at=utc_now(),
            ),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            raise conflict()
        if kind == "ai":
            self.session.execute(
                update(WorkspaceConnectionState)
                .where(
                    WorkspaceConnectionState.workspace_id == self.workspace_id,
                    WorkspaceConnectionState.default_ai_provider_id == connection_id,
                )
                .values(
                    default_ai_provider_id=None,
                    revision=WorkspaceConnectionState.revision + 1,
                ),
                execution_options={"synchronize_session": False},
            )

    def snapshot(self, kind: str, connection_id: str) -> ConnectionSnapshot:
        row = self.get(kind, connection_id)
        if not row.enabled:
            raise ServiceError("connection_disabled", "Connection is disabled", 409)
        model_kind = "n8n" if kind == "n8n" else row.kind
        api_key = ""
        if model_kind != "mock":
            if not row.api_key_ciphertext:
                raise ServiceError(
                    "connection_unconfigured", "Connection has no stored API key", 409
                )
            api_key = self.cipher.decrypt(
                row.api_key_ciphertext,
                workspace_id=self.workspace_id,
                connection_id=connection_id,
            )
        return ConnectionSnapshot(
            workspace_id=self.workspace_id,
            id=row.id,
            revision=row.revision,
            kind=model_kind,
            name=row.name,
            base_url=row.base_url,
            model=getattr(row, "model", None),
            api_key=SecretStr(api_key),
        )

    def state(self) -> WorkspaceConnectionState:
        state = self.session.get(
            WorkspaceConnectionState, self.workspace_id, populate_existing=True
        )
        if state is None:
            state = WorkspaceConnectionState(
                workspace_id=self.workspace_id,
                default_ai_provider_id=None,
                revision=1,
            )
            self.session.add(state)
            self.session.flush()
        return state

    def clear_default_if(self, provider_id: str) -> None:
        self.session.execute(
            update(WorkspaceConnectionState)
            .where(
                WorkspaceConnectionState.workspace_id == self.workspace_id,
                WorkspaceConnectionState.default_ai_provider_id == provider_id,
            )
            .values(
                default_ai_provider_id=None,
                revision=WorkspaceConnectionState.revision + 1,
            ),
            execution_options={"synchronize_session": False},
        )

    def set_default(self, provider_id: str, expected_state_revision: int) -> int:
        provider = self.get("ai", provider_id)
        if not provider.enabled:
            raise ServiceError("connection_disabled", "Connection is disabled", 409)
        if provider.kind != "mock" and not provider.api_key_ciphertext:
            raise ServiceError("connection_unconfigured", "Connection has no stored API key", 409)
        state = self.state()
        result = self.session.execute(
            update(WorkspaceConnectionState)
            .where(
                WorkspaceConnectionState.workspace_id == self.workspace_id,
                WorkspaceConnectionState.revision == expected_state_revision,
            )
            .values(
                default_ai_provider_id=provider_id,
                revision=WorkspaceConnectionState.revision + 1,
            ),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            raise conflict()
        self.session.expire(state)
        return state.revision


def ensure_key_check(session, cipher) -> ConnectionKeyCheck:
    marker = session.get(ConnectionKeyCheck, MARKER_ID)
    if marker is None:
        marker = ConnectionKeyCheck(
            id=MARKER_ID,
            ciphertext=cipher.encrypt(
                KEY_CHECK_SECRET,
                workspace_id=MARKER_WORKSPACE,
                connection_id=MARKER_CONNECTION,
            ),
        )
        session.add(marker)
        session.flush()
    else:
        cipher.decrypt(
            marker.ciphertext,
            workspace_id=MARKER_WORKSPACE,
            connection_id=MARKER_CONNECTION,
        )
    return marker


def encrypted_row_count(session) -> int:
    total = 0
    for model in (N8nInstance, AIProvider):
        total += int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.api_key_ciphertext.is_not(None))
            )
            or 0
        )
    return total


def verify_keyring_startup(factory, cipher, *, demo_mode: bool = False) -> None:
    """Fail closed when stored credentials cannot be decrypted with this keyring."""
    if demo_mode:
        return
    with factory() as session:
        marker = session.get(ConnectionKeyCheck, MARKER_ID)
        encrypted = encrypted_row_count(session)
        if cipher is None:
            if marker is not None or encrypted:
                raise RuntimeError(
                    "Connection encryption is required for stored credentials"
                )
            return
        if marker is None:
            if encrypted:
                raise RuntimeError(
                    "Encrypted connections exist without an encryption key check"
                )
            return
        cipher.decrypt(
            marker.ciphertext,
            workspace_id=MARKER_WORKSPACE,
            connection_id=MARKER_CONNECTION,
        )
