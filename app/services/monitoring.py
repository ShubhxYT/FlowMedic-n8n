import asyncio
import logging
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from app.core.errors import ServiceError
from app.core.security import sanitize
from app.integrations.n8n.client import Execution, ExecutionPage, is_failed
from app.repositories.incidents import IncidentRepository
from app.repositories.monitoring import ExecutionHistoryRepository, MonitoringRepository, is_expired
from app.schemas.domain import MonitoringStatus, MonitoringSyncResult
from app.services.incidents import normalize

logger = logging.getLogger(__name__)
SOURCE = "n8n"
SOURCE_IDENTIFIER = "default"
TRANSIENT_CODES = {
    "n8n_timeout",
    "n8n_unreachable",
    "n8n_rate_limited",
    "n8n_server_error",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def execution_success(status: str | None) -> bool | None:
    if status == "success":
        return True
    if status in {"error", "crashed"}:
        return False
    return None


class MonitoringService:
    """Read-only fresh polling with a bounded, durable historical backfill."""

    def __init__(self, settings, session_factory, n8n, alerts=None):
        self.settings = settings
        self.session_factory = session_factory
        self.n8n = n8n
        self.alerts = alerts
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._owner_id = str(uuid.uuid4())
        self._state = "disabled" if not self.enabled else "idle"
        self._stopping = False

    @property
    def enabled(self) -> bool:
        return bool(
            not self.settings.demo_mode
            and self.settings.n8n_base_url
            and self.settings.n8n_api_key.get_secret_value()
        )

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="flowmedic-monitor")
        logger.info(
            "monitoring_loop_started interval_seconds=%s",
            self.settings.monitor_poll_interval_seconds,
        )

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        logger.info("monitoring_loop_stopped")

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self.settings.monitor_poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            await self.sync()

    def status(self) -> MonitoringStatus:
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).existing_checkpoint(
                SOURCE, SOURCE_IDENTIFIER
            )
            if checkpoint is None:
                lease_state = "unclaimed"
            elif checkpoint.lease_expires_at is not None and not is_expired(
                checkpoint.lease_expires_at
            ):
                lease_state = "active" if checkpoint.lease_owner_id == self._owner_id else "standby"
            else:
                lease_state = "unclaimed"
            return MonitoringStatus(
                enabled=self.enabled,
                state="disabled" if not self.enabled else self._state,
                poll_interval_seconds=self.settings.monitor_poll_interval_seconds,
                last_checked_at=checkpoint.last_checked_at if checkpoint else None,
                last_fresh_poll_at=checkpoint.last_fresh_poll_at if checkpoint else None,
                last_successful_sync_at=checkpoint.last_successful_sync_at if checkpoint else None,
                last_error_at=checkpoint.last_error_at if checkpoint else None,
                last_error_summary=checkpoint.last_error_summary if checkpoint else None,
                consecutive_failure_count=checkpoint.consecutive_failure_count if checkpoint else 0,
                backfill_pending=bool(checkpoint and checkpoint.backfill_cursor),
                backfill_completed_at=checkpoint.backfill_completed_at if checkpoint else None,
                backfill_state=(
                    "disabled"
                    if self.settings.monitor_backfill_pages_per_cycle == 0
                    else "pending"
                    if checkpoint and checkpoint.backfill_cursor
                    else "complete"
                ),
                lease_state=lease_state,
                retention_days=self.settings.execution_history_retention_days,
                last_retention_at=checkpoint.last_retention_at if checkpoint else None,
            )

    async def sync(self) -> MonitoringSyncResult:
        if not self.enabled:
            return MonitoringSyncResult(status="disabled")
        if self._lock.locked():
            return MonitoringSyncResult(status="skipped")
        async with self._lock:
            if not self._acquire_lease():
                self._state = "idle"
                logger.info("lease_contended")
                return MonitoringSyncResult(status="skipped")
            was_degraded = self._state == "degraded"
            self._state = "running"
            try:
                result = await self._sync_with_retries()
            except ServiceError as exc:
                self._record_failure(exc)
                self._state = "degraded"
                self._evaluate_alerts(monitoring_state="degraded")
                logger.warning("monitoring_poll_failed code=%s", exc.code)
                return MonitoringSyncResult(status="failed")
            except Exception:
                self._record_failure(
                    ServiceError("monitoring_error", "Monitoring poll failed", 503)
                )
                self._state = "degraded"
                self._evaluate_alerts(monitoring_state="degraded")
                logger.exception("monitoring_poll_failed code=monitoring_error")
                return MonitoringSyncResult(status="failed")
            finally:
                self._release_lease()
            self._state = "idle"
            self._evaluate_alerts(monitoring_state="idle", workflow_health=True)
            if was_degraded:
                logger.info("monitoring_recovered")
            logger.info(
                (
                    "monitoring_poll_completed executions_discovered=%s "
                    "history_records_created=%s incidents_created=%s"
                ),
                result.executions_discovered,
                result.history_records_created,
                result.incidents_created,
            )
            return result

    def _acquire_lease(self) -> bool:
        with self.session_factory() as session:
            acquired, recovered = MonitoringRepository(session).acquire_lease(
                SOURCE,
                SOURCE_IDENTIFIER,
                self._owner_id,
                self.settings.monitor_lease_seconds,
            )
        if acquired:
            logger.info("lease_acquired")
            if recovered:
                logger.info("lease_recovered")
        return acquired

    def _release_lease(self) -> None:
        with self.session_factory() as session:
            MonitoringRepository(session).release_lease(SOURCE, SOURCE_IDENTIFIER, self._owner_id)

    async def _sync_with_retries(self) -> MonitoringSyncResult:
        for attempt in range(self.settings.monitor_max_retries + 1):
            try:
                self._heartbeat_lease()
                return await self._sync_cycle()
            except ServiceError as exc:
                if exc.code not in TRANSIENT_CODES or attempt >= self.settings.monitor_max_retries:
                    raise
                delay = self.settings.monitor_retry_base_seconds * (2**attempt)
                logger.warning(
                    "monitoring_retry attempt=%s delay_seconds=%s code=%s",
                    attempt + 1,
                    delay,
                    exc.code,
                )
                self._heartbeat_lease()
                await asyncio.sleep(delay)
        raise ServiceError("monitoring_error", "Monitoring poll failed", 503)

    async def _sync_cycle(self) -> MonitoringSyncResult:
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).checkpoint(SOURCE, SOURCE_IDENTIFIER)
            prior_backfill_cursor = checkpoint.backfill_cursor or checkpoint.cursor_value
            session.commit()

        logger.info("fresh_poll_started")
        fresh_page = await self.n8n.recent_executions(self.settings.monitor_page_size, None)
        fresh_entries = await self._hydrate(fresh_page)
        fresh_counts = self._persist_fresh_page(fresh_entries, fresh_page, prior_backfill_cursor)
        logger.info("fresh_poll_completed executions_discovered=%s", len(fresh_entries))

        discovered, history_created, incidents_created = fresh_counts
        cursor = prior_backfill_cursor
        if cursor is None or self.settings.monitor_backfill_pages_per_cycle == 0:
            logger.info("backfill_skipped")
        else:
            logger.info("backfill_started")
            for _ in range(self.settings.monitor_backfill_pages_per_cycle):
                if cursor is None:
                    break
                self._heartbeat_lease()
                page = await self.n8n.recent_executions(self.settings.monitor_page_size, cursor)
                entries = await self._hydrate(page)
                counts, cursor = self._persist_backfill_page(entries, page)
                discovered += counts[0]
                history_created += counts[1]
                incidents_created += counts[2]
            logger.info("backfill_completed pending=%s", bool(cursor))

        self._run_retention_if_due()
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).checkpoint(SOURCE, SOURCE_IDENTIFIER)
            return MonitoringSyncResult(
                status="completed",
                executions_discovered=discovered,
                history_records_created=history_created,
                incidents_created=incidents_created,
                next_cursor=checkpoint.backfill_cursor,
            )

    async def _hydrate(self, page: ExecutionPage) -> list[Execution]:
        entries: list[Execution] = []
        for execution in page.data:
            if execution.status is None or is_failed(execution):
                entries.append(await self.n8n.execution_details(execution.id))
            else:
                entries.append(execution)
        return entries

    def _persist_fresh_page(
        self,
        entries: list[Execution],
        page: ExecutionPage,
        prior_backfill_cursor: str | None,
    ) -> tuple[int, int, int]:
        counts = self._persist_entries(entries)
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).checkpoint(SOURCE, SOURCE_IDENTIFIER)
            now = utc_now()
            if checkpoint.backfill_cursor is None and checkpoint.cursor_value:
                checkpoint.backfill_cursor = checkpoint.cursor_value
            checkpoint.cursor_value = None
            if prior_backfill_cursor is None:
                checkpoint.backfill_cursor = page.nextCursor
                checkpoint.backfill_completed_at = None if page.nextCursor else now
            checkpoint.last_execution_id = (
                entries[-1].id if entries else checkpoint.last_execution_id
            )
            checkpoint.last_checked_at = now
            checkpoint.last_fresh_poll_at = now
            checkpoint.last_successful_sync_at = now
            checkpoint.last_error_at = None
            checkpoint.last_error_summary = None
            checkpoint.consecutive_failure_count = 0
            checkpoint.updated_at = now
            session.commit()
        return len(entries), *counts

    def _persist_backfill_page(
        self,
        entries: list[Execution],
        page: ExecutionPage,
    ) -> tuple[tuple[int, int, int], str | None]:
        counts = self._persist_entries(entries)
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).checkpoint(SOURCE, SOURCE_IDENTIFIER)
            now = utc_now()
            checkpoint.backfill_cursor = page.nextCursor
            if page.nextCursor is None:
                checkpoint.backfill_completed_at = now
            checkpoint.last_successful_sync_at = now
            checkpoint.last_error_at = None
            checkpoint.last_error_summary = None
            checkpoint.consecutive_failure_count = 0
            checkpoint.updated_at = now
            session.commit()
        return (len(entries), *counts), page.nextCursor

    def _persist_entries(self, entries: list[Execution]) -> tuple[int, int]:
        with self.session_factory() as session:
            history = ExecutionHistoryRepository(session)
            incidents = IncidentRepository(session)
            created_history = 0
            created_incidents = 0
            new_incidents: list[tuple[str, str]] = []
            secrets = self._secrets()
            for detail in entries:
                record, inserted = history.create_once(
                    source=SOURCE,
                    workflow_id=detail.workflowId,
                    workflow_name=sanitize(self._workflow_name(detail), secrets),
                    execution_id=detail.id,
                    execution_status=detail.status,
                    recorded_at=utc_now(),
                    started_at=detail.startedAt,
                    stopped_at=detail.stoppedAt,
                    is_success=execution_success(detail.status),
                )
                created_history += int(inserted)
                if is_failed(detail):
                    incident, created = incidents.create_once(normalize(detail, secrets))
                    history.attach_incident(record, incident.id)
                    created_incidents += int(created)
                    if created:
                        new_incidents.append((incident.id, incident.workflow_id))
            session.commit()
        for incident_id, workflow_id in new_incidents:
            self._evaluate_alerts(
                incident_id=incident_id,
                workflow_id=workflow_id,
            )
        if entries:
            logger.info("monitoring_executions_discovered count=%s", len(entries))
        else:
            logger.info("monitoring_no_new_data")
        return created_history, created_incidents

    def _evaluate_alerts(
        self,
        *,
        incident_id: str | None = None,
        workflow_id: str | None = None,
        monitoring_state: str | None = None,
        workflow_health: bool = False,
    ) -> None:
        if self.alerts is None:
            return
        try:
            if incident_id is not None and workflow_id is not None:
                self.alerts.on_new_incident(incident_id, workflow_id)
            if monitoring_state is not None:
                self.alerts.evaluate_monitoring_state(monitoring_state)
            if workflow_health:
                self.alerts.evaluate_workflow_health()
        except Exception:
            # Alerting must never prevent read-only monitoring persistence.
            logger.exception("alert_evaluation_failed")

    def _heartbeat_lease(self) -> None:
        with self.session_factory() as session:
            active = MonitoringRepository(session).heartbeat_lease(
                SOURCE,
                SOURCE_IDENTIFIER,
                self._owner_id,
                self.settings.monitor_lease_seconds,
            )
        if not active:
            raise ServiceError("monitoring_lease_lost", "Monitoring ownership lease was lost", 503)

    def _run_retention_if_due(self) -> None:
        with self.session_factory() as session:
            checkpoints = MonitoringRepository(session)
            checkpoint = checkpoints.checkpoint(SOURCE, SOURCE_IDENTIFIER)
            now = utc_now()
            if checkpoint.last_retention_at is not None and now - (
                checkpoint.last_retention_at.replace(tzinfo=UTC)
                if checkpoint.last_retention_at.tzinfo is None
                else checkpoint.last_retention_at.astimezone(UTC)
            ) < timedelta(seconds=self.settings.retention_cleanup_interval_seconds):
                return
            logger.info("retention_started")
            cutoff = now - timedelta(days=self.settings.execution_history_retention_days)
            deleted = ExecutionHistoryRepository(session).delete_recorded_before(
                cutoff, self.settings.retention_cleanup_batch_size
            )
            checkpoint.last_retention_at = now
            checkpoint.updated_at = now
            session.commit()
            logger.info("retention_deleted_count count=%s", deleted)
            logger.info("retention_completed")

    def _record_failure(self, error: ServiceError) -> None:
        with self.session_factory() as session:
            checkpoint = MonitoringRepository(session).checkpoint(SOURCE, SOURCE_IDENTIFIER)
            now = utc_now()
            checkpoint.last_checked_at = now
            checkpoint.last_error_at = now
            checkpoint.last_error_summary = error.message[:500]
            checkpoint.consecutive_failure_count += 1
            checkpoint.updated_at = now
            session.commit()

    def _secrets(self) -> tuple[str, ...]:
        return tuple(
            key.get_secret_value()
            for key in (
                self.settings.n8n_api_key,
                self.settings.ai_api_key,
                self.settings.flowmedic_api_key,
            )
        )

    @staticmethod
    def _workflow_name(execution: Execution) -> str:
        value = (execution.workflowData or {}).get("name")
        return value if isinstance(value, str) and value else "Unknown workflow"
