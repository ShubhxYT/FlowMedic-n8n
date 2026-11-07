import asyncio
import logging
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from app.core.errors import ServiceError
from app.core.security import sanitize
from app.integrations.n8n.client import Execution, ExecutionPage, is_failed
from app.repositories.incidents import IncidentRepository
from app.repositories.monitoring import (
    ExecutionHistoryRepository,
    MonitoringRepository,
    fence_poll_write,
    is_expired,
)
from app.schemas.domain import MonitoringStatus, MonitoringSyncResult
from app.services.incidents import normalize

logger = logging.getLogger(__name__)
SOURCE = "n8n"
TRANSIENT_CODES = {
    "n8n_timeout",
    "n8n_unreachable",
    "n8n_rate_limited",
    "n8n_server_error",
}
WORKFLOW_NAMES_TTL = 300
WORKFLOW_NAMES_PAGE_SIZE = 250
WORKFLOW_NAMES_MAX_PAGES = 4


def utc_now() -> datetime:
    return datetime.now(UTC)


def execution_success(status: str | None) -> bool | None:
    if status == "success":
        return True
    if status in {"error", "crashed"}:
        return False
    return None


class MonitoringService:
    """One read-only poller scoped to a single workspace and n8n instance."""

    def __init__(self, settings, session_factory, n8n, snapshot, alerts=None, poll_slots=None):
        self.settings = settings
        self.session_factory = session_factory
        self.n8n = n8n
        self.snapshot = snapshot
        self.alerts = alerts
        self.workspace_id = snapshot.workspace_id
        self.instance_id = snapshot.id
        self.snapshot_revision = snapshot.revision
        self.poll_slots = poll_slots
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._sync_tasks: set[asyncio.Task] = set()
        self._owner_id = str(uuid.uuid4())
        self._state = "idle"
        self._stopping = False
        self._lease_lost = False
        if alerts is not None:
            alerts.write_fence = self._fence
            alerts.transient_secrets = self._secrets()
        self._names_cache: tuple[float, dict[str, str]] | None = None

    @property
    def enabled(self) -> bool:
        return True

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name=f"flowmedic-monitor-{self.instance_id}")
        logger.info(
            "monitoring_loop_started instance_id=%s interval_seconds=%s",
            self.instance_id,
            self.settings.monitor_poll_interval_seconds,
        )

    async def stop(self) -> None:
        self._stopping = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        pending = list(self._sync_tasks)
        for operation in pending:
            operation.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._release_lease()
        logger.info("monitoring_loop_stopped instance_id=%s", self.instance_id)

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                try:
                    await asyncio.sleep(self.settings.monitor_poll_interval_seconds)
                except asyncio.CancelledError:
                    raise
                try:
                    await self.sync()
                except Exception:
                    logger.warning(
                        "monitoring_loop_iteration_failed instance_id=%s", self.instance_id
                    )
        finally:
            if not self._stopping:
                logger.error("monitoring_loop_exited_unexpectedly instance_id=%s", self.instance_id)
            self._task = None

    def _session(self):
        if not hasattr(self.session_factory, "workspace_id"):
            raise RuntimeError("Monitoring requires a workspace-scoped factory")
        return self.session_factory()

    def status(self) -> MonitoringStatus:
        with self._session() as session:
            repository = MonitoringRepository(session, self.workspace_id, self.instance_id)
            checkpoint = repository.existing_checkpoint(SOURCE)
            if checkpoint is None:
                lease_state = "unclaimed"
            elif checkpoint.lease_expires_at is not None and not is_expired(
                checkpoint.lease_expires_at
            ):
                lease_state = "active" if checkpoint.lease_owner_id == self._owner_id else "standby"
            else:
                lease_state = "unclaimed"
            return MonitoringStatus(
                workspace_id=self.workspace_id,
                instance_id=self.instance_id,
                instance_name=self.snapshot.name,
                enabled=True,
                state=self._state,
                poll_interval_seconds=self.settings.monitor_poll_interval_seconds,
                last_checked_at=checkpoint.last_checked_at if checkpoint else None,
                last_fresh_poll_at=checkpoint.last_fresh_poll_at if checkpoint else None,
                last_successful_sync_at=(
                    checkpoint.last_successful_sync_at if checkpoint else None
                ),
                last_error_at=checkpoint.last_error_at if checkpoint else None,
                last_error_summary=checkpoint.last_error_summary if checkpoint else None,
                consecutive_failure_count=(
                    checkpoint.consecutive_failure_count if checkpoint else 0
                ),
                backfill_pending=bool(checkpoint and checkpoint.backfill_cursor),
                backfill_completed_at=(checkpoint.backfill_completed_at if checkpoint else None),
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
        if self._lock.locked():
            return MonitoringSyncResult(
                status="skipped", workspace_id=self.workspace_id, instance_id=self.instance_id
            )
        task = asyncio.current_task()
        self._sync_tasks.add(task)
        try:
            async with self._lock:
                if self.poll_slots is None:
                    return await self._sync_owned()
                async with self.poll_slots:
                    return await self._sync_owned()
        finally:
            self._sync_tasks.discard(task)

    async def _sync_owned(self) -> MonitoringSyncResult:
        guard = getattr(self.n8n, "guard", None)
        if guard is not None:
            guard()
        if not self._acquire_lease():
            self._state = "idle"
            logger.info("lease_contended instance_id=%s", self.instance_id)
            return MonitoringSyncResult(
                status="skipped", workspace_id=self.workspace_id, instance_id=self.instance_id
            )
        was_degraded = self._state == "degraded"
        self._state = "running"
        self._lease_lost = False
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(asyncio.current_task()),
            name=f"flowmedic-heartbeat-{self.instance_id}",
        )
        try:
            result = await self._sync_with_retries()
            await self._evaluate_alerts(monitoring_state="idle", workflow_health=True)
        except asyncio.CancelledError:
            if self._lease_lost:
                self._state = "idle"
                raise ServiceError(
                    "monitoring_lease_lost", "Monitoring ownership changed", 409
                ) from None
            raise
        except ServiceError as exc:
            if exc.code in {"connection_changed", "monitoring_lease_lost"}:
                self._state = "idle"
                logger.warning(
                    "monitoring_stopped instance_id=%s code=%s", self.instance_id, exc.code
                )
                raise
            self._record_failure(exc)
            self._state = "degraded"
            await self._evaluate_alerts(monitoring_state="degraded")
            logger.warning(
                "monitoring_poll_failed instance_id=%s code=%s", self.instance_id, exc.code
            )
            return MonitoringSyncResult(
                status="failed", workspace_id=self.workspace_id, instance_id=self.instance_id
            )
        except Exception:
            self._record_failure(ServiceError("monitoring_error", "Monitoring poll failed", 503))
            self._state = "degraded"
            await self._evaluate_alerts(monitoring_state="degraded")
            logger.warning(
                "monitoring_poll_failed instance_id=%s code=monitoring_error", self.instance_id
            )
            return MonitoringSyncResult(
                status="failed", workspace_id=self.workspace_id, instance_id=self.instance_id
            )
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            self._release_lease()
        self._state = "idle"
        if was_degraded:
            logger.info("monitoring_recovered instance_id=%s", self.instance_id)
        logger.info(
            (
                "monitoring_poll_completed instance_id=%s executions_discovered=%s "
                "history_records_created=%s incidents_created=%s"
            ),
            self.instance_id,
            result.executions_discovered,
            result.history_records_created,
            result.incidents_created,
        )
        return result

    def _acquire_lease(self) -> bool:
        with self._session() as session:
            acquired, recovered = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).acquire_lease(SOURCE, self._owner_id, self.settings.monitor_lease_seconds)
        if acquired:
            logger.info("lease_acquired instance_id=%s", self.instance_id)
            if recovered:
                logger.info("lease_recovered instance_id=%s", self.instance_id)
        return acquired

    def _release_lease(self) -> None:
        try:
            with self._session() as session:
                MonitoringRepository(session, self.workspace_id, self.instance_id).release_lease(
                    SOURCE, self._owner_id
                )
        except Exception:
            logger.warning("lease_release_failed instance_id=%s", self.instance_id)

    async def _heartbeat_loop(self, operation: asyncio.Task) -> None:
        interval = min(10, self.settings.monitor_lease_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                self._heartbeat_lease()
            except Exception:
                self._lease_lost = True
                operation.cancel()
                return

    def _heartbeat_lease(self) -> None:
        if self._lease_lost:
            raise ServiceError("monitoring_lease_lost", "Monitoring ownership lease was lost", 409)
        with self._session() as session:
            active = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).heartbeat_lease(SOURCE, self._owner_id, self.settings.monitor_lease_seconds)
        if not active:
            self._lease_lost = True
            raise ServiceError("monitoring_lease_lost", "Monitoring ownership lease was lost", 409)

    def _fence(self, session) -> None:
        fence_poll_write(
            session,
            workspace_id=self.workspace_id,
            instance_id=self.instance_id,
            revision=self.snapshot_revision,
            owner_id=self._owner_id,
            lease_seconds=self.settings.monitor_lease_seconds,
        )

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
                    "monitoring_retry instance_id=%s attempt=%s delay_seconds=%s code=%s",
                    self.instance_id,
                    attempt + 1,
                    delay,
                    exc.code,
                )
                self._heartbeat_lease()
                await asyncio.sleep(delay)
        raise ServiceError("monitoring_error", "Monitoring poll failed", 503)

    async def _sync_cycle(self) -> MonitoringSyncResult:
        with self._session() as session:
            repository = MonitoringRepository(session, self.workspace_id, self.instance_id)
            checkpoint = repository.checkpoint(SOURCE)
            prior_backfill_cursor = checkpoint.backfill_cursor or checkpoint.cursor_value
            session.commit()

        workflow_names = await self._workflow_names()

        logger.info("fresh_poll_started instance_id=%s", self.instance_id)
        fresh_page = await self.n8n.recent_executions(self.settings.monitor_page_size, None)
        fresh_entries = await self._hydrate(fresh_page)
        fresh_counts = await self._persist_fresh_page(
            fresh_entries, fresh_page, prior_backfill_cursor, workflow_names
        )
        logger.info(
            "fresh_poll_completed instance_id=%s executions_discovered=%s",
            self.instance_id,
            len(fresh_entries),
        )

        discovered, history_created, incidents_created = fresh_counts
        cursor = prior_backfill_cursor
        if cursor is None or self.settings.monitor_backfill_pages_per_cycle == 0:
            logger.info("backfill_skipped instance_id=%s", self.instance_id)
        else:
            logger.info("backfill_started instance_id=%s", self.instance_id)
            for _ in range(self.settings.monitor_backfill_pages_per_cycle):
                if cursor is None:
                    break
                self._heartbeat_lease()
                page = await self.n8n.recent_executions(self.settings.monitor_page_size, cursor)
                entries = await self._hydrate(page)
                counts, cursor = await self._persist_backfill_page(entries, page, workflow_names)
                discovered += counts[0]
                history_created += counts[1]
                incidents_created += counts[2]
            logger.info(
                "backfill_completed instance_id=%s pending=%s",
                self.instance_id,
                bool(cursor),
            )

        self._run_retention_if_due()
        with self._session() as session:
            checkpoint = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).checkpoint(SOURCE)
            return MonitoringSyncResult(
                status="completed",
                workspace_id=self.workspace_id,
                instance_id=self.instance_id,
                executions_discovered=discovered,
                history_records_created=history_created,
                incidents_created=incidents_created,
                next_cursor=checkpoint.backfill_cursor,
            )

    async def _hydrate(self, page: ExecutionPage) -> list[Execution]:
        entries: list[Execution] = []
        for execution in page.data:
            if execution.status is None or is_failed(execution):
                self._heartbeat_lease()
                try:
                    entries.append(await self.n8n.execution_details(execution.id))
                except ServiceError as exc:
                    if exc.status_code not in {404, 410}:
                        raise
                    logger.warning(
                        "monitoring_execution_detail_unavailable execution_id=%s status_code=%s",
                        execution.id,
                        exc.status_code,
                    )
                    entries.append(
                        Execution(
                            id=execution.id,
                            workflowId=execution.workflowId,
                            status=None,
                            startedAt=execution.startedAt,
                            stoppedAt=execution.stoppedAt,
                        )
                    )
            else:
                entries.append(execution)
        return entries

    async def _workflow_names(self) -> dict[str, str]:
        """Resolve workflow ids to names, cached for WORKFLOW_NAMES_TTL seconds.

        Failures are non-fatal: monitoring must keep working even if the workflow
        listing endpoint is unavailable or the test double does not implement it.
        """
        now = time.monotonic()
        if self._names_cache is not None and now - self._names_cache[0] < WORKFLOW_NAMES_TTL:
            return self._names_cache[1]
        fetch = getattr(self.n8n, "workflows", None)
        if fetch is None:
            return {}
        names: dict[str, str] = {}
        cursor = None
        try:
            for _ in range(WORKFLOW_NAMES_MAX_PAGES):
                page = await fetch(limit=WORKFLOW_NAMES_PAGE_SIZE, cursor=cursor)
                for workflow in page.data:
                    names[workflow.id] = workflow.name
                cursor = page.nextCursor
                if not cursor:
                    break
        except Exception:
            logger.warning("workflow_names_unavailable")
            return self._names_cache[1] if self._names_cache else {}
        self._names_cache = (now, names)
        return names

    async def _persist_fresh_page(
        self,
        entries: list[Execution],
        page: ExecutionPage,
        prior_backfill_cursor: str | None,
        workflow_names: dict[str, str] | None = None,
    ) -> tuple[int, int, int]:
        new_incidents = []
        with self._session() as session:
            self._fence(session)
            counts = self._persist_entries(
                entries,
                session=session,
                new_incidents=new_incidents,
                workflow_names=workflow_names,
            )
            checkpoint = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).checkpoint(SOURCE)
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
        for incident_id, workflow_id in new_incidents:
            await self._evaluate_alerts(incident_id=incident_id, workflow_id=workflow_id)
        return len(entries), *counts

    async def _persist_backfill_page(
        self,
        entries: list[Execution],
        page: ExecutionPage,
        workflow_names: dict[str, str] | None = None,
    ) -> tuple[tuple[int, int, int], str | None]:
        new_incidents = []
        with self._session() as session:
            self._fence(session)
            counts = self._persist_entries(
                entries,
                session=session,
                new_incidents=new_incidents,
                workflow_names=workflow_names,
            )
            checkpoint = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).checkpoint(SOURCE)
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
        for incident_id, workflow_id in new_incidents:
            await self._evaluate_alerts(incident_id=incident_id, workflow_id=workflow_id)
        return (len(entries), *counts), page.nextCursor

    def _persist_entries(
        self, entries, *, session=None, new_incidents=None, workflow_names=None
    ) -> tuple[int, int]:
        if session is None:
            with self._session() as owned:
                self._fence(owned)
                counts = self._persist_entries(
                    entries,
                    session=owned,
                    new_incidents=new_incidents,
                    workflow_names=workflow_names,
                )
                owned.commit()
                return counts
        history = ExecutionHistoryRepository(session, self.workspace_id, self.instance_id)
        incidents = IncidentRepository(session, self.workspace_id, self.instance_id)
        created_history = created_incidents = 0
        secrets = self._secrets()
        for detail in entries:
            record, inserted = history.create_once(
                source=SOURCE,
                workflow_id=detail.workflowId,
                workflow_name=sanitize(self._workflow_name(detail, workflow_names), secrets),
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
                if created and new_incidents is not None:
                    new_incidents.append((incident.id, incident.workflow_id))
        logger.info(
            "monitoring_executions_discovered instance_id=%s count=%s",
            self.instance_id,
            len(entries),
        )
        return created_history, created_incidents

    async def _evaluate_alerts(
        self,
        *,
        incident_id: str | None = None,
        workflow_id: str | None = None,
        monitoring_state: str | None = None,
        workflow_health: bool = False,
    ) -> None:
        if self.alerts is None:
            return

        def evaluate():
            try:
                if incident_id is not None and workflow_id is not None:
                    self.alerts.on_new_incident(incident_id, workflow_id)
                if monitoring_state is not None:
                    self.alerts.evaluate_monitoring_state(monitoring_state)
                if workflow_health:
                    self.alerts.evaluate_workflow_health()
            except ServiceError:
                raise
            except Exception:
                logger.warning("alert_evaluation_failed instance_id=%s", self.instance_id)

        delivery = asyncio.create_task(asyncio.to_thread(evaluate))
        try:
            await asyncio.shield(delivery)
        except asyncio.CancelledError:
            # The thread may own a delivery; shutdown must await it before closing pools.
            with suppress(ServiceError):
                await delivery
            raise

    def _run_retention_if_due(self) -> None:
        with self._session() as session:
            repository = MonitoringRepository(session, self.workspace_id, self.instance_id)
            checkpoint = repository.checkpoint(SOURCE)
            now = utc_now()
            last = checkpoint.last_retention_at
            if last is not None and now - (
                last.replace(tzinfo=UTC) if last.tzinfo is None else last.astimezone(UTC)
            ) < timedelta(seconds=self.settings.retention_cleanup_interval_seconds):
                return
            self._fence(session)
            logger.info("retention_started instance_id=%s", self.instance_id)
            cutoff = now - timedelta(days=self.settings.execution_history_retention_days)
            deleted = ExecutionHistoryRepository(
                session, self.workspace_id, self.instance_id
            ).delete_recorded_before(cutoff, self.settings.retention_cleanup_batch_size)
            checkpoint.last_retention_at = now
            checkpoint.updated_at = now
            session.commit()
            logger.info(
                "retention_deleted_count instance_id=%s count=%s",
                self.instance_id,
                deleted,
            )
            logger.info("retention_completed instance_id=%s", self.instance_id)

    def _record_failure(self, error: ServiceError) -> None:
        with self._session() as session:
            self._fence(session)
            checkpoint = MonitoringRepository(
                session, self.workspace_id, self.instance_id
            ).checkpoint(SOURCE)
            now = utc_now()
            checkpoint.last_checked_at = now
            checkpoint.last_error_at = now
            checkpoint.last_error_summary = sanitize(error.message, self._secrets())[:500]
            checkpoint.consecutive_failure_count += 1
            checkpoint.updated_at = now
            session.commit()

    def _secrets(self) -> tuple[str, ...]:
        return (
            self.snapshot.api_key.get_secret_value(),
            self.settings.flowmedic_api_key.get_secret_value(),
        )

    @staticmethod
    def _workflow_name(execution: Execution, workflow_names: dict[str, str] | None = None) -> str:
        value = (execution.workflowData or {}).get("name")
        if not (isinstance(value, str) and value):
            value = (workflow_names or {}).get(execution.workflowId)
        return value if isinstance(value, str) and value else "Unknown workflow"
