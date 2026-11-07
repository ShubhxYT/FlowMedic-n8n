"""Transactional connection management, safe probes, and durable test budgets."""

import asyncio
import json
import time
from urllib.parse import quote

from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert

from app.core.errors import ServiceError
from app.integrations.n8n.client import N8nWorkflowPage
from app.models.connections import ConnectionTestBucket, WorkspaceConnectionState
from app.repositories.audit import record_event
from app.repositories.connections import ConnectionRepository, ensure_key_check
from app.schemas.settings import ConnectionOut, ConnectionPage, ConnectionTestResult


def invalid(message="Invalid connection settings"):
    return ServiceError("invalid_connection", message, 422)


def require_revision() -> ServiceError:
    return ServiceError("connection_changed", "Connection changed; reload and retry", 409)


def structured_probe(model: str) -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Return an object with ok set to true."}],
        "max_completion_tokens": 32,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "connection_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"ok": {"type": "boolean", "enum": [True]}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
            },
        },
    }


class ConnectionService:
    def __init__(self, factory, cipher, outbound, notify_changed, drain=None):
        self.factory = factory
        self.cipher = cipher
        self.outbound = outbound
        self.notify_changed = notify_changed
        self.drain = drain
        self.test_slots = asyncio.Semaphore(4)

    # -- reads ---------------------------------------------------------
    def list(self, context, kind: str) -> ConnectionPage:
        with self.factory() as session:
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            rows = repository.list(kind)
            state = session.get(WorkspaceConnectionState, context.workspace_id)
            default_id = state.default_ai_provider_id if state else None
            revision = state.revision if state else 1
            return ConnectionPage(
                items=[self._out(kind, row, default_id) for row in rows],
                state_revision=revision,
            )

    def _out(self, kind: str, row, default_id) -> ConnectionOut:
        return ConnectionOut(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            kind="n8n" if kind == "n8n" else row.kind,
            model=getattr(row, "model", None),
            enabled=bool(row.enabled),
            revision=row.revision,
            has_api_key=bool(row.api_key_ciphertext),
            is_default=bool(kind == "ai" and row.id == default_id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    # -- validation ----------------------------------------------------
    def _require_cipher(self):
        if self.cipher is None:
            raise ServiceError(
                "connection_encryption_unavailable",
                "Connection encryption is unavailable; contact the operator",
                503,
            )

    @staticmethod
    def _reject_nulls(payload):
        for field in ("name", "base_url", "enabled", "kind", "model"):
            if field in payload.model_fields_set and getattr(payload, field) is None:
                raise invalid(f"{field.replace('_', ' ').title()} is required")

    async def _effective(self, kind: str, payload, existing):
        self._reject_nulls(payload)
        fields = payload.model_fields_set
        name = payload.name if "name" in fields else getattr(existing, "name", None)
        if not name:
            raise invalid("Display name is required")
        if kind == "n8n" and any(
            field in fields
            for field in ("kind", "model", "make_default", "expected_state_revision")
        ):
            raise invalid("AI-only fields are not accepted for n8n connections")

        enabled = (
            payload.enabled
            if "enabled" in fields
            else bool(getattr(existing, "enabled", False))
        )
        if payload.remove_api_key:
            enabled = False

        if kind == "n8n":
            base = payload.base_url if "base_url" in fields else getattr(existing, "base_url", None)
            if not base:
                raise invalid("Base URL is required")
            base = await self.outbound.policy.validate(base)
            previous = getattr(existing, "base_url", None)
            if previous and previous != base and not payload.confirm_same_server:
                raise ServiceError(
                    "same_server_confirmation_required",
                    "Confirm that the edited URL is the same server",
                    422,
                )
            key_plain = None
            if "api_key" in fields and payload.api_key is not None:
                key_plain = payload.api_key.get_secret_value()
            elif not payload.remove_api_key and getattr(existing, "api_key_ciphertext", None):
                key_plain = self.cipher.decrypt(
                    existing.api_key_ciphertext,
                    workspace_id=getattr(existing, "workspace_id", None),
                    connection_id=existing.id,
                )
            return {
                "kind": "n8n",
                "mock": False,
                "name": name,
                "base_url": base,
                "model": None,
                "enabled": enabled,
                "key_plain": key_plain,
                "replace_key": "api_key" in fields and payload.api_key is not None,
                "remove_key": bool(payload.remove_api_key),
            }

        provider_kind = (
            payload.kind if "kind" in fields else getattr(existing, "kind", "openai-compatible")
        )
        if provider_kind == "mock":
            return {
                "kind": "mock",
                "mock": True,
                "name": name,
                "base_url": None,
                "model": None,
                "enabled": enabled,
                "key_plain": None,
                "replace_key": False,
                "remove_key": bool(getattr(existing, "api_key_ciphertext", None))
                or bool(payload.remove_api_key),
            }

        base = payload.base_url if "base_url" in fields else getattr(existing, "base_url", None)
        model = payload.model if "model" in fields else getattr(existing, "model", None)
        if not base or not model:
            raise invalid("Base URL and model are required for a live provider")
        base = await self.outbound.policy.validate(base)
        previous = getattr(existing, "base_url", None)
        if previous and previous != base and not payload.confirm_same_server:
            raise ServiceError(
                "same_server_confirmation_required",
                "Confirm that the edited URL is the same server",
                422,
            )
        key_plain = None
        if "api_key" in fields and payload.api_key is not None:
            key_plain = payload.api_key.get_secret_value()
        elif not payload.remove_api_key and getattr(existing, "api_key_ciphertext", None):
            key_plain = self.cipher.decrypt(
                existing.api_key_ciphertext,
                workspace_id=getattr(existing, "workspace_id", None),
                connection_id=existing.id,
            )
        if enabled and not key_plain:
            raise ServiceError(
                "connection_key_required", "An API key is required before enabling", 422
            )
        return {
            "kind": "openai-compatible",
            "mock": False,
            "name": name,
            "base_url": base,
            "model": model,
            "enabled": enabled,
            "key_plain": key_plain,
            "replace_key": "api_key" in fields and payload.api_key is not None,
            "remove_key": bool(payload.remove_api_key),
        }

    def _write_values(self, kind: str, effective: dict) -> dict:
        values = {"name": effective["name"]}
        if kind == "n8n":
            values["base_url"] = effective["base_url"]
        else:
            values["base_url"] = effective["base_url"]
            values["kind"] = effective["kind"]
            values["model"] = effective["model"]
        if effective["remove_key"]:
            values["remove_api_key"] = True
        elif effective["replace_key"]:
            values["api_key"] = effective["key_plain"]
        return values

    # -- mutations -----------------------------------------------------
    async def create(self, context, kind: str, payload) -> ConnectionOut:
        self._require_cipher()
        if payload.enabled:
            raise ServiceError(
                "save_disabled_first", "Save the connection disabled, then enable it", 422
            )
        if payload.make_default:
            raise invalid("Save and enable the provider before selecting it as default")
        effective = await self._effective(kind, payload, None)
        if not effective["mock"] and not effective["key_plain"]:
            raise ServiceError(
                "connection_key_required", "An API key is required", 422
            )
        with self.factory() as session:
            ensure_key_check(session, self.cipher)
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            row = repository.create(kind, self._write_values(kind, effective))
            state = repository.state()
            record_event(
                session,
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                action="connection.create",
                target_id=row.id,
                outcome="success",
            )
            session.commit()
            result = self._out(kind, row, state.default_ai_provider_id)
        self.notify_changed()
        return result

    async def patch(self, context, kind: str, connection_id: str, payload) -> ConnectionOut:
        self._require_cipher()
        with self.factory() as session:
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            existing = repository.get(kind, connection_id)
        effective = await self._effective(kind, payload, existing)
        with self.factory() as session:
            ensure_key_check(session, self.cipher)
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            values = self._write_values(kind, effective)
            values["enabled"] = effective["enabled"]
            if not effective["mock"] and effective["enabled"] and not effective["key_plain"]:
                raise ServiceError(
                    "connection_key_required", "An API key is required before enabling", 422
                )
            row = repository.update(kind, connection_id, payload.expected_revision, values)
            if kind == "ai" and (
                not row.enabled
                or (row.kind != "mock" and not row.api_key_ciphertext)
                or row.deleted
            ):
                repository.clear_default_if(connection_id)
            if payload.make_default:
                repository.set_default(connection_id, payload.expected_state_revision)
            state = repository.state()
            record_event(
                session,
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                action="connection.update",
                target_id=connection_id,
                outcome="success",
            )
            session.commit()
            result = self._out(kind, row, state.default_ai_provider_id)
        self.notify_changed()
        if kind == "n8n" and self.drain is not None and not result.enabled:
            await self.drain(context.workspace_id, connection_id)
        return result

    async def delete(self, context, kind: str, connection_id: str, expected_revision: int):
        self._require_cipher()
        with self.factory() as session:
            ensure_key_check(session, self.cipher)
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            repository.soft_delete(kind, connection_id, expected_revision)
            record_event(
                session,
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                action="connection.delete",
                target_id=connection_id,
                outcome="success",
            )
            session.commit()
        self.notify_changed()
        if kind == "n8n" and self.drain is not None:
            await self.drain(context.workspace_id, connection_id)

    # -- tests ---------------------------------------------------------
    def _reserve_test_budget(self, workspace_id: str, actor_id: str) -> None:
        minute = int(time.time()) // 60 * 60
        with self.factory() as session:
            session.execute(
                delete(ConnectionTestBucket).where(
                    ConnectionTestBucket.minute_start < minute - 86400
                )
            )
            for key, limit in ((actor_id or "*", 5), ("*", 30)):
                statement = insert(ConnectionTestBucket).values(
                    workspace_id=workspace_id, actor_id=key, minute_start=minute, count=1
                )
                session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[
                            ConnectionTestBucket.workspace_id,
                            ConnectionTestBucket.actor_id,
                            ConnectionTestBucket.minute_start,
                        ],
                        set_={"count": ConnectionTestBucket.count + 1},
                    )
                )
                row = session.get(
                    ConnectionTestBucket,
                    (workspace_id, key, minute),
                    populate_existing=True,
                )
                if row.count > limit:
                    session.commit()
                    raise ServiceError(
                        "connection_test_rate_limited",
                        "Too many connection tests; try again later",
                        429,
                    )
            session.commit()

    def _audit_test(self, context, connection_id, outcome):
        with self.factory() as session:
            record_event(
                session,
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                action="connection.test",
                target_id=connection_id,
                outcome=outcome,
            )
            session.commit()

    def _guard(self, context, kind: str, connection_id: str, expected_revision: int):
        def guard():
            with self.factory() as session:
                repository = ConnectionRepository(session, context.workspace_id, self.cipher)
                try:
                    current = repository.get(kind, connection_id)
                except ServiceError:
                    raise require_revision() from None
                if current.revision != expected_revision:
                    raise require_revision()

        return guard

    @staticmethod
    def _check(response):
        if response.status_code in {401, 403}:
            raise ServiceError(
                "connection_auth_failed", "Upstream rejected the tested credentials", 422
            )
        if response.status_code == 429:
            raise ServiceError(
                "upstream_rate_limited", "Upstream rate limited the test", 429
            )
        if 200 <= response.status_code < 300:
            return
        raise ServiceError("connection_test_failed", "Connection test failed", 422)

    async def _probe(self, kind, effective, mode, acknowledged, guard):
        if effective["mock"]:
            return "mock", "The deterministic mock provider performs no network request."
        base = effective["base_url"]
        key = effective["key_plain"]
        if kind == "n8n":
            root = base if base.endswith("/api/v1") else base.rstrip("/") + "/api/v1"
            response = await self.outbound.get(
                root,
                "/workflows",
                params={"limit": 1},
                headers={"X-N8N-API-KEY": key},
                guard=guard,
            )
            self._check(response)
            try:
                N8nWorkflowPage.model_validate(response.json())
            except (ValueError, ValidationError):
                raise ServiceError(
                    "connection_test_failed", "Connection test failed", 422
                ) from None
            return "workflows_read", "The tested key can read workflows."
        if mode == "structured_output":
            if not acknowledged:
                raise ServiceError(
                    "structured_output_ack_required",
                    "Acknowledge the usage cost before testing structured output",
                    422,
                )
            response = await self.outbound.post(
                base,
                "/chat/completions",
                headers={"Authorization": "Bearer " + key},
                json=structured_probe(effective["model"]),
                guard=guard,
            )
            self._check(response)
            try:
                content = response.json()["choices"][0]["message"]["content"]
                if json.loads(content) != {"ok": True}:
                    raise ValueError
            except (ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError):
                raise ServiceError(
                    "connection_test_failed", "Connection test failed", 422
                ) from None
            return (
                "structured_output",
                "A small structured-output request succeeded and may be billable.",
            )
        response = await self.outbound.get(
            base,
            "/models/" + quote(effective["model"], safe=""),
            headers={"Authorization": "Bearer " + key},
            guard=guard,
        )
        if response.status_code in {405, 501}:
            raise ServiceError(
                "ai_access_test_unsupported",
                "This provider does not expose a model access endpoint",
                422,
            )
        self._check(response)
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("id") != effective["model"]:
                raise ValueError
        except (ValueError, TypeError):
            raise ServiceError(
                "ai_access_test_unsupported",
                "This provider does not expose a compatible model access endpoint",
                422,
            ) from None
        return "model_access", "The tested key can access the selected model."

    async def test_candidate(self, context, kind: str, payload) -> ConnectionTestResult:
        self._require_cipher()
        self._reserve_test_budget(context.workspace_id, context.user_id)
        effective = await self._effective(kind, payload.candidate, None)
        if not effective["mock"] and not effective["key_plain"]:
            raise ServiceError("connection_key_required", "An API key is required", 422)
        try:
            async with self.test_slots:
                capability, message = await self._probe(
                    kind, effective, payload.mode, payload.acknowledge_usage_cost, None
                )
        except ServiceError:
            self._audit_test(context, None, "failure")
            raise
        self._audit_test(context, None, "success")
        return ConnectionTestResult(
            ok=True, capability=capability, tested_revision=None, message=message
        )

    async def test_saved(self, context, kind: str, connection_id: str, payload):
        self._require_cipher()
        self._reserve_test_budget(context.workspace_id, context.user_id)
        with self.factory() as session:
            repository = ConnectionRepository(session, context.workspace_id, self.cipher)
            existing = repository.get(kind, connection_id)
        if payload.expected_revision != existing.revision:
            raise require_revision()
        effective = await self._effective(kind, payload.changes, existing)
        if not effective["mock"] and not effective["key_plain"]:
            raise ServiceError("connection_key_required", "An API key is required", 422)
        guard = self._guard(context, kind, connection_id, payload.expected_revision)
        try:
            async with self.test_slots:
                capability, message = await self._probe(
                    kind, effective, payload.mode, payload.acknowledge_usage_cost, guard
                )
        except ServiceError:
            self._audit_test(context, connection_id, "failure")
            raise
        self._audit_test(context, connection_id, "success")
        return ConnectionTestResult(
            ok=True,
            capability=capability,
            tested_revision=existing.revision,
            message=message,
        )
