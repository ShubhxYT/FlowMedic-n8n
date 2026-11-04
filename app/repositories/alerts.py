from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.incident import (
    AlertConditionState,
    AlertDeliveryAttempt,
    AlertEvent,
    AlertRule,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


class AlertRuleRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_rules(self) -> list[AlertRule]:
        return list(self.session.scalars(select(AlertRule).order_by(AlertRule.created_at.desc())))

    def get(self, rule_id: str) -> AlertRule | None:
        return self.session.get(AlertRule, rule_id)

    def enabled_for(self, trigger_type: str) -> list[AlertRule]:
        return list(
            self.session.scalars(
                select(AlertRule)
                .where(AlertRule.trigger_type == trigger_type, AlertRule.enabled.is_(True))
                .order_by(AlertRule.created_at)
            )
        )

    def create(self, **values) -> AlertRule:
        now = utc_now()
        rule = AlertRule(created_at=now, updated_at=now, **values)
        self.session.add(rule)
        self.session.flush()
        return rule

    def update(self, rule: AlertRule, **values) -> AlertRule:
        for key, value in values.items():
            setattr(rule, key, value)
        rule.updated_at = utc_now()
        self.session.flush()
        return rule


class AlertEventRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_events(self, limit: int, offset: int) -> list[AlertEvent]:
        return list(
            self.session.scalars(
                select(AlertEvent)
                .order_by(AlertEvent.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )

    def get(self, event_id: str) -> AlertEvent | None:
        return self.session.get(AlertEvent, event_id)

    def in_cooldown(
        self,
        rule_id: str,
        deduplication_key: str,
        now: datetime,
    ) -> AlertEvent | None:
        return self.session.scalar(
            select(AlertEvent)
            .where(
                AlertEvent.rule_id == rule_id,
                AlertEvent.deduplication_key == deduplication_key,
                AlertEvent.cooldown_until > now,
            )
            .order_by(AlertEvent.created_at.desc())
        )

    def create_once(self, **values) -> tuple[AlertEvent, bool]:
        existing = self.session.scalar(
            select(AlertEvent).where(AlertEvent.event_key == values["event_key"])
        )
        if existing is not None:
            return existing, False
        event = AlertEvent(**values)
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
        except IntegrityError:
            existing = self.session.scalar(
                select(AlertEvent).where(AlertEvent.event_key == values["event_key"])
            )
            if existing is None:
                raise
            return existing, False
        return event, True

    def deliveries(self, event_id: str) -> list[AlertDeliveryAttempt]:
        return list(
            self.session.scalars(
                select(AlertDeliveryAttempt)
                .where(AlertDeliveryAttempt.alert_event_id == event_id)
                .order_by(AlertDeliveryAttempt.attempt_number)
            )
        )

    def attempt(self, attempt_id: str) -> AlertDeliveryAttempt | None:
        return self.session.get(AlertDeliveryAttempt, attempt_id)

    def create_attempt(self, **values) -> AlertDeliveryAttempt:
        attempt = AlertDeliveryAttempt(**values)
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def reserve_next_attempt(
        self,
        event_id: str,
        provider: str,
        max_attempts: int,
        started_at: datetime,
    ) -> AlertDeliveryAttempt | None:
        """Reserve one unique attempt number, retrying only conflicting reservations."""
        for _ in range(max_attempts):
            event = self.get(event_id)
            if event is None or event.status == "delivered":
                return None
            next_attempt = len(self.deliveries(event_id)) + 1
            if next_attempt > max_attempts:
                return None
            try:
                with self.session.begin_nested():
                    attempt = self.create_attempt(
                        alert_event_id=event.id,
                        provider=provider,
                        attempt_number=next_attempt,
                        status="pending",
                        error_summary=None,
                        started_at=started_at,
                        completed_at=None,
                    )
            except IntegrityError:
                # Another process reserved this number first. Re-read durable state before retrying.
                self.session.expire_all()
                continue
            return attempt
        return None


class AlertConditionStateRepository:
    def __init__(self, session: Session):
        self.session = session

    def transition(self, condition_type: str, scope_key: str, current_state: str) -> bool:
        state = self.session.scalar(
            select(AlertConditionState).where(
                AlertConditionState.condition_type == condition_type,
                AlertConditionState.scope_key == scope_key,
            )
        )
        now = utc_now()
        if state is None:
            self.session.add(
                AlertConditionState(
                    condition_type=condition_type,
                    scope_key=scope_key,
                    current_state=current_state,
                    updated_at=now,
                )
            )
            self.session.flush()
            return True
        changed = state.current_state != current_state
        state.current_state = current_state
        state.updated_at = now
        return changed
