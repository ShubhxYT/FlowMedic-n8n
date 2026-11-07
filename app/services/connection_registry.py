import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from sqlalchemy import select

from app.core.errors import ServiceError
from app.models.connections import N8nInstance, WorkspaceConnectionState
from app.repositories.connections import ConnectionRepository, ConnectionSnapshot

logger = logging.getLogger(__name__)


def read_enabled_snapshots(factory, cipher) -> list[ConnectionSnapshot]:
    """One short read; a single undecryptable record must not hide healthy ones."""
    with factory() as session:
        rows = session.scalars(
            select(N8nInstance).where(N8nInstance.enabled.is_(True), N8nInstance.deleted.is_(False))
        ).all()
        snapshots = []
        for row in rows:
            try:
                snapshots.append(
                    ConnectionRepository(session, row.workspace_id, cipher).snapshot("n8n", row.id)
                )
            except ServiceError:
                logger.error("connection_snapshot_failed connection_id=%s", row.id)
        return snapshots


def read_default_ai(factory, cipher, workspace_id: str) -> ConnectionSnapshot | None:
    with factory() as session:
        state = session.get(WorkspaceConnectionState, workspace_id)
        if state is None or not state.default_ai_provider_id:
            return None
        return ConnectionRepository(session, workspace_id, cipher).snapshot(
            "ai", state.default_ai_provider_id
        )


def snapshot_guard(factory, model, snapshot: ConnectionSnapshot):
    def guard():
        with factory() as session:
            row = session.get(model, snapshot.id)
            if (
                row is None
                or row.workspace_id != snapshot.workspace_id
                or row.deleted
                or not row.enabled
                or row.revision != snapshot.revision
                or (snapshot.kind != "mock" and not row.api_key_ciphertext)
            ):
                raise ServiceError(
                    "connection_changed", "Connection changed; retry the operation", 409
                )

    return guard


def revision_guard(factory, snapshot: ConnectionSnapshot):
    return snapshot_guard(factory, N8nInstance, snapshot)


class ConnectionRegistry:
    def __init__(
        self,
        read_enabled: Callable[[], list[ConnectionSnapshot]],
        build_worker: Callable[[ConnectionSnapshot, asyncio.Semaphore], Any],
        *,
        reconcile_seconds: float = 5,
        concurrency: int = 4,
    ):
        self.read_enabled = read_enabled
        self.build_worker = build_worker
        self.reconcile_seconds = reconcile_seconds
        self.poll_slots = asyncio.Semaphore(concurrency)
        self.workers: dict[tuple[str, str], tuple[int, Any]] = {}
        self.wake = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.reconcile_lock = asyncio.Lock()

    def notify_changed(self) -> None:
        self.wake.set()

    def get(self, workspace_id: str, instance_id: str):
        entry = self.workers.get((workspace_id, instance_id))
        return entry[1] if entry else None

    async def start(self) -> None:
        if self.task is not None and not self.task.done():
            return
        await self.reconcile()
        self.task = asyncio.create_task(self._loop(), name="connection-reconcile")

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        async with self.reconcile_lock:
            workers, self.workers = list(self.workers.values()), {}
            await asyncio.gather(*(self._stop_worker(worker) for _, worker in workers))

    async def _stop_worker(self, worker) -> None:
        try:
            async with asyncio.timeout(10):
                await worker.stop()
        except TimeoutError:
            logger.error("connection_worker_stop_failed code=timeout")
            raise

    async def _replace(self, key, snapshot) -> None:
        current = self.workers.get(key)
        if current is not None:
            try:
                await self._stop_worker(current[1])
            except Exception:
                logger.error("connection_worker_replace_failed code=stop_failed")
                return
            self.workers.pop(key, None)
        if snapshot is not None:
            try:
                worker = self.build_worker(snapshot, self.poll_slots)
                await worker.start()
                self.workers[key] = (snapshot.revision, worker)
            except Exception:
                logger.error("connection_worker_start_failed code=start_failed")

    async def reconcile(self) -> None:
        async with self.reconcile_lock:
            snapshots = self.read_enabled()
            desired = {(item.workspace_id, item.id): item for item in snapshots}
            changes = []
            for key in set(desired) | set(self.workers):
                item = desired.get(key)
                current = self.workers.get(key)
                if item is not None and current is not None and current[0] == item.revision:
                    continue
                changes.append(self._replace(key, item))
            await asyncio.gather(*changes)

    async def drain(self, workspace_id: str, instance_id: str) -> None:
        async with self.reconcile_lock:
            key = (workspace_id, instance_id)
            current = self.workers.get(key)
            if current is not None:
                await self._stop_worker(current[1])
                self.workers.pop(key, None)
        self.notify_changed()

    async def _loop(self) -> None:
        while True:
            try:
                async with asyncio.timeout(self.reconcile_seconds):
                    await self.wake.wait()
            except TimeoutError:
                pass
            self.wake.clear()
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("connection_reconcile_failed code=configuration_read_failed")
