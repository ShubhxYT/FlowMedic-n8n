import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.core.security import sanitize
from app.models.connections import N8nInstance
from app.models.incident import ExecutionHistory, Incident
from app.repositories.alerts import (
    AlertConditionStateRepository,
    AlertEventRepository,
    AlertRuleRepository,
)
from app.services.health import build_workflow_health

logger = logging.getLogger(__name__)

TRIGGER_NEW_INCIDENT = "new_incident"
TRIGGER_WORKFLOW_UNHEALTHY = "workflow_unhealthy"
TRIGGER_MONITORING_DEGRADED = "monitoring_degraded"


def utc_now() -> datetime:
    return datetime.now(UTC)


def scoped_alert_key(*parts: str | int) -> str:
    encoded = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class DeliveryPayload:
    event_id: str
    instance_id: str
    trigger_type: str
    incident_id: str | None
    workflow_id: str | None
    event_created_at: datetime
    attempt_number: int
    instance_name: str | None = None


class AlertDeliveryError(Exception):
    """A delivery failure that carries only a safe, retryable classification."""

    def __init__(self, summary: str, *, retryable: bool):
        super().__init__(summary)
        self.summary = summary
        self.retryable = retryable


class AlertDeliveryProvider:
    """Future delivery adapters must keep this boundary free of raw incident payloads."""

    name = "provider"

    def deliver(self, payload: DeliveryPayload) -> None:
        raise NotImplementedError


class MockAlertDeliveryProvider(AlertDeliveryProvider):
    """Local-only provider used for tests and safe development; it sends nothing externally."""

    name = "mock"

    def deliver(self, payload: DeliveryPayload) -> None:
        logger.info(
            "alert_mock_delivered event_id=%s trigger_type=%s",
            payload.event_id,
            payload.trigger_type,
        )


class WebhookAlertDeliveryProvider(AlertDeliveryProvider):
    """HTTPS-only webhook adapter with a deliberately minimal event payload."""

    name = "webhook"

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        signing_secret: str = "",
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = url
        self.signing_secret = signing_secret
        self.client = httpx.Client(
            transport=transport,
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def deliver(self, payload: DeliveryPayload) -> None:
        body = json.dumps(
            {
                "version": 1,
                "event": {
                    "id": payload.event_id,
                    "instance_id": payload.instance_id,
                    "instance_name": payload.instance_name,
                    "trigger_type": payload.trigger_type,
                    "incident_id": payload.incident_id,
                    "workflow_id": payload.workflow_id,
                    "created_at": payload.event_created_at.isoformat(),
                },
                "delivery": {"attempt": payload.attempt_number},
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        timestamp = str(int(utc_now().timestamp()))
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "FlowMedic-Alert/1",
            "X-FlowMedic-Timestamp": timestamp,
        }
        if self.signing_secret:
            signed = f"{timestamp}.".encode() + body
            signature = hmac.new(self.signing_secret.encode(), signed, hashlib.sha256).hexdigest()
            headers["X-FlowMedic-Signature"] = f"v1={signature}"
        try:
            with self.client.stream("POST", self.url, content=body, headers=headers) as response:
                status_code = response.status_code
        except httpx.TimeoutException as exc:
            raise AlertDeliveryError("Webhook request timed out", retryable=True) from exc
        except httpx.RequestError as exc:
            raise AlertDeliveryError("Webhook request failed", retryable=True) from exc
        if 200 <= status_code < 300:
            return
        if status_code == 429 or 500 <= status_code < 600:
            raise AlertDeliveryError("Webhook returned a retryable response", retryable=True)
        raise AlertDeliveryError("Webhook returned a non-retryable response", retryable=False)

    def close(self) -> None:
        self.client.close()


class AlertService:
    def __init__(
        self,
        settings,
        session_factory,
        workspace_id: str,
        instance_id: str,
        provider: AlertDeliveryProvider | None = None,
        *,
        providers: dict[str, AlertDeliveryProvider] | None = None,
    ):
        from app.core.workspaces import require_workspace

        with session_factory() as session:
            if require_workspace(session) != workspace_id:
                raise ValueError("Alerts session is outside the workspace")
        self.write_fence = None
        self.transient_secrets = ()
        self.settings = settings
        self.workspace_id = workspace_id
        self.instance_id = instance_id
        self.session_factory = session_factory
        self.providers = providers or {"mock": provider or MockAlertDeliveryProvider()}

    @property
    def enabled(self) -> bool:
        return not self.settings.demo_mode

    def _fence(self, session):
        if self.write_fence is not None:
            self.write_fence(session)

    def provider_available(self, provider_name: str) -> bool:
        return self.enabled and provider_name in self.providers

    def close(self) -> None:
        for provider in self.providers.values():
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def on_new_incident(self, incident_id: str, workflow_id: str) -> int:
        return self._trigger(
            TRIGGER_NEW_INCIDENT,
            deduplication_key=scoped_alert_key(
                self.workspace_id,
                self.instance_id,
                TRIGGER_NEW_INCIDENT,
                f"incident:{incident_id}",
            ),
            incident_id=incident_id,
            workflow_id=workflow_id,
        )

    def evaluate_workflow_health(self) -> int:
        if not self.enabled:
            return 0
        with self.session_factory() as session:
            self._fence(session)
            histories = session.scalars(
                select(ExecutionHistory).where(ExecutionHistory.instance_id == self.instance_id)
            ).all()
            incidents = session.scalars(
                select(Incident).where(Incident.instance_id == self.instance_id)
            ).all()
            health_rows = build_workflow_health(histories, incidents)
            states = AlertConditionStateRepository(session)
            unhealthy_workflows = []
            for row in health_rows:
                changed = states.transition(
                    TRIGGER_WORKFLOW_UNHEALTHY,
                    row.workflow_id,
                    row.health,
                    self.instance_id,
                )
                if changed and row.health == "unhealthy":
                    unhealthy_workflows.append(row.workflow_id)
            session.commit()
        return sum(
            self._trigger(
                TRIGGER_WORKFLOW_UNHEALTHY,
                deduplication_key=scoped_alert_key(
                    self.workspace_id,
                    self.instance_id,
                    TRIGGER_WORKFLOW_UNHEALTHY,
                    f"workflow:{workflow_id}",
                ),
                workflow_id=workflow_id,
            )
            for workflow_id in unhealthy_workflows
        )

    def evaluate_monitoring_state(self, state: str) -> int:
        if not self.enabled:
            return 0
        with self.session_factory() as session:
            self._fence(session)
            changed = AlertConditionStateRepository(session).transition(
                TRIGGER_MONITORING_DEGRADED,
                "monitoring",
                state,
                self.instance_id,
            )
            session.commit()
        if changed and state == "degraded":
            return self._trigger(
                TRIGGER_MONITORING_DEGRADED,
                deduplication_key=scoped_alert_key(
                    self.workspace_id,
                    self.instance_id,
                    TRIGGER_MONITORING_DEGRADED,
                    "monitoring",
                ),
            )
        return 0

    def _trigger(
        self,
        trigger_type: str,
        *,
        deduplication_key: str,
        incident_id: str | None = None,
        workflow_id: str | None = None,
    ) -> int:
        if not self.enabled:
            return 0
        created_event_ids: list[str] = []
        now = utc_now()
        with self.session_factory() as session:
            self._fence(session)
            rules = AlertRuleRepository(session).enabled_for(trigger_type, self.instance_id)
            events = AlertEventRepository(session)
            for rule in rules:
                if (
                    events.in_cooldown(
                        rule.id,
                        deduplication_key,
                        now,
                        instance_id=self.instance_id,
                    )
                    is not None
                ):
                    continue
                event_key = self._event_key(rule.id, deduplication_key, now, rule.cooldown_seconds)
                event, created = events.create_once(
                    instance_id=self.instance_id,
                    rule_id=rule.id,
                    trigger_type=trigger_type,
                    event_key=event_key,
                    deduplication_key=deduplication_key,
                    incident_id=incident_id,
                    workflow_id=workflow_id,
                    status="pending",
                    cooldown_until=now + timedelta(seconds=rule.cooldown_seconds),
                    last_error_summary=None,
                    created_at=now,
                    delivered_at=None,
                )
                if created:
                    created_event_ids.append(event.id)
            session.commit()
        for event_id in created_event_ids:
            self.deliver(event_id)
        return len(created_event_ids)

    def deliver(self, event_id: str) -> None:
        if not self.enabled:
            return
        for _ in range(self.settings.alert_max_retries):
            with self.session_factory() as session:
                self._fence(session)
                events = AlertEventRepository(session)
                event = events.get(event_id)
                if event is None:
                    return
                rule = AlertRuleRepository(session).get(event.rule_id)
                provider = self.providers.get(rule.delivery_provider) if rule is not None else None
                if provider is None:
                    event.status = "failed"
                    event.last_error_summary = "Alert delivery provider is not configured"
                    session.commit()
                    logger.warning("alert_provider_unavailable event_id=%s", event_id)
                    return
                attempt = events.reserve_next_attempt(
                    event_id,
                    provider.name,
                    self.settings.alert_max_retries,
                    utc_now(),
                )
                if attempt is None:
                    return
                session.commit()
                attempt_id = attempt.id
                instance = session.get(N8nInstance, event.instance_id)
                payload = DeliveryPayload(
                    event_id=event.id,
                    instance_id=event.instance_id,
                    instance_name=sanitize(instance.name, self.transient_secrets)
                    if instance
                    else None,
                    trigger_type=event.trigger_type,
                    incident_id=event.incident_id,
                    workflow_id=event.workflow_id,
                    event_created_at=event.created_at,
                    attempt_number=attempt.attempt_number,
                )
            try:
                provider.deliver(payload)
            except AlertDeliveryError as exc:
                with self.session_factory() as session:
                    self._fence(session)
                    event = AlertEventRepository(session).get(event_id)
                    attempt = AlertEventRepository(session).attempt(attempt_id)
                    if event is None or attempt is None:
                        return
                    attempt.status = "failed"
                    attempt.error_summary = exc.summary
                    attempt.completed_at = utc_now()
                    event.status = "failed"
                    event.last_error_summary = exc.summary
                    session.commit()
                logger.warning(
                    "alert_delivery_failed event_id=%s attempt=%s",
                    event_id,
                    attempt.attempt_number,
                )
                if not exc.retryable:
                    return
                continue
            except Exception:
                with self.session_factory() as session:
                    self._fence(session)
                    event = AlertEventRepository(session).get(event_id)
                    attempt = AlertEventRepository(session).attempt(attempt_id)
                    if event is None or attempt is None:
                        return
                    attempt.status = "failed"
                    attempt.error_summary = "Alert delivery provider failed"
                    attempt.completed_at = utc_now()
                    event.status = "failed"
                    event.last_error_summary = "Alert delivery provider failed"
                    session.commit()
                logger.warning(
                    "alert_delivery_failed event_id=%s attempt=%s",
                    event_id,
                    attempt.attempt_number,
                )
                continue
            with self.session_factory() as session:
                self._fence(session)
                event = AlertEventRepository(session).get(event_id)
                attempt = AlertEventRepository(session).attempt(attempt_id)
                if event is None or attempt is None:
                    return
                attempt.status = "delivered"
                attempt.completed_at = utc_now()
                event.status = "delivered"
                event.delivered_at = attempt.completed_at
                event.last_error_summary = None
                session.commit()
            logger.info(
                "alert_delivery_completed event_id=%s attempt=%s",
                event_id,
                attempt.attempt_number,
            )
            return

    def _event_key(
        self, rule_id: str, deduplication_key: str, now: datetime, cooldown_seconds: int
    ) -> str:
        window_start = int(now.timestamp()) // cooldown_seconds
        return scoped_alert_key(
            self.workspace_id, self.instance_id, rule_id, deduplication_key, window_start
        )
