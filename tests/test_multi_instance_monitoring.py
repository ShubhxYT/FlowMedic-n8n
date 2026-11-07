"""Managed polling races: real DB fences, permitted mock HTTP, isolated identities."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from auth_support import OperatorClient, legacy_factory
from connection_support import CIPHER, KEYRING, outbound, provision
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.integrations.n8n.client import Execution, N8nClient
from app.main import create_app
from app.models.auth import LEGACY_WORKSPACE_ID, Workspace
from app.models.connections import N8nInstance
from app.models.incident import (
    AlertConditionState,
    AlertEvent,
    ExecutionHistory,
    Incident,
)
from app.repositories.alerts import AlertConditionStateRepository, AlertRuleRepository
from app.repositories.connections import ConnectionRepository
from app.repositories.monitoring import MonitoringRepository
from app.services.alerts import AlertService
from app.services.connection_registry import (
    ConnectionRegistry,
    read_enabled_snapshots,
    revision_guard,
)
from app.services.health import build_workflow_health
from app.services.monitoring import MonitoringService


@pytest.fixture
def store(tmp_path):
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'instances.db'}",
        monitor_max_retries=0,
        monitor_poll_interval_seconds=60,
        connection_encryption_keys=KEYRING,
        connection_active_key_id="test",
        deployment_environment="development",
        outbound_destinations='[{"origin":"https://8.8.8.8"}]',
    )
    upgrade_database(settings.database_url)
    engine, factory = create_database(settings.database_url)
    scoped = legacy_factory(factory)
    first = provision(factory, api_key="alpha-key")
    second = provision(factory, reuse=False, name="Second", api_key="beta-key")
    yield SimpleNamespace(
        settings=settings, engine=engine, factory=factory, scoped=scoped, first=first, second=second
    )
    engine.dispose()


def worker(store, snap, client, slots=None, alerts=True):
    service = (
        AlertService(store.settings, store.scoped, snap.workspace_id, snap.id) if alerts else None
    )
    return MonitoringService(
        store.settings, store.scoped, client, snap, poll_slots=slots, alerts=service
    )


def records(store, model):
    with store.factory() as db:
        return list(db.scalars(select(model)))


def add_rules(store):
    with store.scoped() as db:
        for trigger in ("new_incident", "workflow_unhealthy", "monitoring_degraded"):
            AlertRuleRepository(db).create(
                name=trigger,
                trigger_type=trigger,
                enabled=True,
                cooldown_seconds=300,
                delivery_provider="mock",
            )
        # A rule pinned to one instance cannot fire for another instance.
        AlertRuleRepository(db).create(
            name="pinned",
            trigger_type="new_incident",
            enabled=True,
            instance_id=store.first.id,
            cooldown_seconds=300,
            delivery_provider="mock",
        )
        db.commit()


def failure(execution_id="same-execution"):
    return Execution(
        id=execution_id,
        workflowId="same-workflow",
        status="error",
        workflowData={"name": "Same workflow"},
        data={"resultData": {"error": {"message": "401 Unauthorized"}}},
    )


def test_reused_ids_have_separate_history_health_cursors_alerts_and_leases(store):
    add_rules(store)
    requests = []

    def handler(request):
        requests.append(request)
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers
        assert "x-unrelated" not in request.headers
        key = request.headers["X-N8N-API-KEY"]
        assert key in {"alpha-key", "beta-key"}
        if request.url.path.endswith("/executions"):
            return httpx.Response(
                200,
                json={"data": [failure().model_dump(mode="json")], "nextCursor": key + "-cursor"},
            )
        return httpx.Response(
            200,
            json=failure().model_dump(mode="json"),
            headers={"set-cookie": "private=must-not-cross"},
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"X-Unrelated": "private"},
            cookies={"old": "cookie"},
        ) as http:
            slots = asyncio.Semaphore(2)
            services = [
                worker(
                    store,
                    snap,
                    N8nClient(snap, outbound(http), revision_guard(store.factory, snap)),
                    slots,
                )
                for snap in (store.first, store.second)
            ]
            results = await asyncio.gather(*(service.sync() for service in services))
            assert [result.incidents_created for result in results] == [1, 1]
            assert {s.status().backfill_pending for s in services} == {True}
            # Four execution calls plus one cached workflow-name page per instance.
            assert len(requests) == 6
        histories, incidents = records(store, ExecutionHistory), records(store, Incident)
        assert len(histories) == len(incidents) == 2
        assert {row.execution_id for row in histories} == {"same-execution"}
        health = build_workflow_health(histories, incidents)
        assert len(health) == 2
        assert {row.instance_id for row in health} == {store.first.id, store.second.id}
        events = records(store, AlertEvent)
        assert len(events) == 5
        assert len({row.event_key for row in events}) == 5
        assert len(records(store, AlertConditionState)) == 4
        with store.scoped() as db:
            for snap in (store.first, store.second):
                repo = MonitoringRepository(db, snap.workspace_id, snap.id)
                checkpoint = repo.existing_checkpoint("n8n")
                assert checkpoint.backfill_cursor == snap.api_key.get_secret_value() + "-cursor"
                assert checkpoint.lease_owner_id is None
                assert repo.acquire_lease("n8n", snap.id, 120)[0]
        # Both leases can be owned concurrently despite identical upstream IDs.
        with store.scoped() as db:
            assert (
                MonitoringRepository(db, store.first.workspace_id, store.first.id)
                .existing_checkpoint("n8n")
                .lease_owner_id
                == store.first.id
            )
            assert (
                MonitoringRepository(db, store.second.workspace_id, store.second.id)
                .existing_checkpoint("n8n")
                .lease_owner_id
                == store.second.id
            )

    asyncio.run(run())


def test_only_one_owner_polls_and_expired_owner_cannot_write_or_alert(store, monkeypatch):
    add_rules(store)

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def handler(request):
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = N8nClient(
                store.first, outbound(http), revision_guard(store.factory, store.first)
            )
            old, other = worker(store, store.first, client), worker(store, store.first, client)
            task = asyncio.create_task(old.sync())
            await entered.wait()
            assert (await other.sync()).status == "skipped"
            assert calls == 1
            future = datetime.now(UTC) + timedelta(minutes=3)
            monkeypatch.setattr("app.repositories.monitoring.utc_now", lambda: future)
            with pytest.raises(ServiceError, match="lease was lost"):
                old._heartbeat_lease()
            with store.scoped() as db:
                repo = MonitoringRepository(db, store.first.workspace_id, store.first.id)
                assert repo.acquire_lease("n8n", other._owner_id, 120) == (True, True)
            with pytest.raises(ServiceError):
                old._persist_entries([failure()])
            with pytest.raises(ServiceError):
                old._record_failure(ServiceError("n8n_timeout", "safe timeout", 504))
            with pytest.raises(ServiceError):
                old.alerts.evaluate_monitoring_state("degraded")
            release.set()
            with pytest.raises(ServiceError) as error:
                await task
            assert error.value.code == "monitoring_lease_lost"
            with store.scoped() as db:
                assert (
                    MonitoringRepository(db, store.first.workspace_id, store.first.id)
                    .existing_checkpoint("n8n")
                    .lease_owner_id
                    == other._owner_id
                )
        assert (
            records(store, Incident)
            == records(store, ExecutionHistory)
            == records(store, AlertEvent)
            == []
        )

    asyncio.run(run())


def test_heartbeat_runs_during_slow_details_and_other_instance_can_finish(store, monkeypatch):
    # Drive heartbeat scheduling and DB time explicitly; no real lease-duration sleep.
    now = [datetime.now(UTC)]
    monkeypatch.setattr("app.repositories.monitoring.utc_now", lambda: now[0])
    import app.services.monitoring as monitoring

    real_asyncio = asyncio

    async def run():
        detail_entered, detail_release = asyncio.Event(), asyncio.Event()
        ticks, renewed = asyncio.Queue(), asyncio.Queue()

        async def controlled_sleep(delay):
            if delay == 10:
                await ticks.get()
            else:
                await real_asyncio.sleep(delay)

        monkeypatch.setattr(
            monitoring,
            "asyncio",
            SimpleNamespace(
                **{
                    **vars(real_asyncio),
                    "sleep": controlled_sleep,
                }
            ),
        )

        async def handler(request):
            key = request.headers["X-N8N-API-KEY"]
            if key == "alpha-key" and not request.url.path.endswith("/executions"):
                detail_entered.set()
                await detail_release.wait()
                return httpx.Response(200, json=failure().model_dump(mode="json"))
            data = [failure().model_dump(mode="json")] if key == "alpha-key" else []
            return httpx.Response(200, json={"data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            slots = asyncio.Semaphore(2)
            slow, healthy = [
                worker(
                    store,
                    snap,
                    N8nClient(snap, outbound(http), revision_guard(store.factory, snap)),
                    slots,
                    alerts=False,
                )
                for snap in (store.first, store.second)
            ]
            original = slow._heartbeat_lease

            def heartbeat():
                original()
                renewed.put_nowait(True)

            slow._heartbeat_lease = heartbeat
            task = asyncio.create_task(slow.sync())
            await detail_entered.wait()
            # Each jump stays within the renewed lease; total exceeds the old lease.
            for _ in range(5):
                while not renewed.empty():
                    renewed.get_nowait()
                now[0] += timedelta(seconds=30)
                ticks.put_nowait(True)
                await renewed.get()
            assert (await healthy.sync()).status == "completed"
            detail_release.set()
            assert (await task).incidents_created == 1

    asyncio.run(run())


@pytest.mark.parametrize("change", ["revision", "disable", "delete"])
def test_blocked_old_revision_cannot_persist_and_guard_blocks_later_calls(store, change):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json={"data": [failure().model_dump(mode="json")]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = N8nClient(
                store.first, outbound(http), revision_guard(store.factory, store.first)
            )
            service = worker(store, store.first, client)
            task = asyncio.create_task(service.sync())
            await entered.wait()
            with store.factory() as db:
                row = db.get(N8nInstance, store.first.id)
                row.revision += 1
                if change == "disable":
                    row.enabled = False
                elif change == "delete":
                    row.deleted, row.enabled, row.api_key_ciphertext = True, False, None
                else:
                    row.base_url = "https://8.8.8.8/new"
                    row.api_key_ciphertext = CIPHER.encrypt(
                        "new-key", workspace_id=row.workspace_id, connection_id=row.id
                    )
                db.commit()
            release.set()
            with pytest.raises(ServiceError) as error:
                await task
            assert error.value.code == "connection_changed"
            with pytest.raises(ServiceError):
                await client.connectivity()
            assert len(calls) == 1  # Summary response cannot trigger stale hydration.
            if change == "revision":
                with store.factory() as db:
                    current = ConnectionRepository(db, store.first.workspace_id, CIPHER).snapshot(
                        "n8n", store.first.id
                    )
                await N8nClient(
                    current, outbound(http), revision_guard(store.factory, current)
                ).recent_executions()
                assert str(calls[-1].url).startswith("https://8.8.8.8/new/api/v1/executions")
                assert calls[-1].headers["X-N8N-API-KEY"] == "new-key"
                assert calls[0].headers["X-N8N-API-KEY"] == "alpha-key"
        assert records(store, Incident) == records(store, ExecutionHistory) == []

    asyncio.run(run())


def test_heartbeat_failure_promptly_cancels_blocked_request(store, monkeypatch):
    import app.services.monitoring as monitoring

    async def run():
        entered, tick = asyncio.Event(), asyncio.Event()

        async def sleep(delay):
            await tick.wait()

        monkeypatch.setattr(
            monitoring, "asyncio", SimpleNamespace(**{**vars(asyncio), "sleep": sleep})
        )

        class Blocked:
            async def recent_executions(self, limit, cursor):
                entered.set()
                await asyncio.Event().wait()

        service = worker(store, store.first, Blocked(), alerts=False)
        task = asyncio.create_task(service.sync())
        await entered.wait()
        with store.scoped() as db:
            checkpoint = MonitoringRepository(
                db, store.first.workspace_id, store.first.id
            ).existing_checkpoint("n8n")
            checkpoint.lease_owner_id = "replacement"
            db.commit()
        tick.set()
        with pytest.raises(ServiceError) as error:
            await asyncio.wait_for(task, 1)
        assert error.value.code == "monitoring_lease_lost"
        assert service._sync_tasks == set()

    asyncio.run(run())


def test_timeout_and_bad_ciphertext_do_not_stop_healthy_instance(store):
    async def run():
        with store.factory() as db:
            corrupt = db.get(N8nInstance, store.first.id)
            corrupt.api_key_ciphertext = "invalid-ciphertext"
            db.commit()
        snapshots = read_enabled_snapshots(store.factory, CIPHER)
        assert [snap.id for snap in snapshots] == [store.second.id]

        def handler(request):
            if request.headers["X-N8N-API-KEY"] == "alpha-key":
                raise httpx.ReadTimeout("private headers", request=request)
            return httpx.Response(200, json={"data": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            services = [
                worker(
                    store,
                    snap,
                    N8nClient(snap, outbound(http), revision_guard(store.factory, snap)),
                    alerts=False,
                )
                for snap in (store.first, store.second)
            ]
            results = await asyncio.gather(*(s.sync() for s in services))
            assert [result.status for result in results] == ["failed", "completed"]
            assert services[0].status().last_error_summary == "n8n request timed out"

    asyncio.run(run())


def test_migrated_cursor_and_condition_state_survive_first_managed_poll(store):
    old = datetime.now(UTC) - timedelta(days=1)
    with store.scoped() as db:
        checkpoint = MonitoringRepository(db, store.first.workspace_id, store.first.id).checkpoint(
            "n8n"
        )
        checkpoint.source_identifier = "default"
        checkpoint.backfill_cursor = "legacy-cursor"
        db.add(
            AlertConditionState(
                instance_id=store.first.id,
                condition_type="monitoring_degraded",
                scope_key="n8n:default",
                current_state="degraded",
                updated_at=old,
            )
        )
        db.commit()
    with store.scoped() as db:
        assert (
            MonitoringRepository(db, store.first.workspace_id, store.first.id)
            .existing_checkpoint("n8n")
            .backfill_cursor
            == "legacy-cursor"
        )
        assert (
            AlertConditionStateRepository(db).transition(
                "monitoring_degraded", "monitoring", "degraded", store.first.id
            )
            is False
        )
        db.commit()
    state = records(store, AlertConditionState)[0]
    assert len(state.scope_key) == 64
    assert state.updated_at.replace(tzinfo=UTC) == old


def test_api_scopes_reused_ids_and_preserves_archived_history(store, monkeypatch):
    add_rules(store)
    requests = []

    def handler(request):
        requests.append(request)
        data = failure().model_dump(mode="json")
        if request.url.path.endswith("/executions"):
            return httpx.Response(200, json={"data": [data]})
        return httpx.Response(200, json=data)

    app = create_app(store.settings, n8n_transport=httpx.MockTransport(handler))
    with OperatorClient(app) as client:
        for snap in (store.first, store.second):
            query = {"instance_id": snap.id}
            assert client.post("/api/v1/incidents/sync", params=query).json()["created"] == 1
            assert len(client.get("/api/v1/incidents", params=query).json()["data"]) == 1
            assert len(client.get("/api/v1/metrics/workflows", params=query).json()["data"]) == 1
            assert (
                client.get("/api/v1/metrics/overview", params=query).json()["recent_failure_count"]
                == 1
            )
            health = client.get("/api/v1/workflows/same-workflow/health", params=query)
            assert health.json()["instance_id"] == snap.id
            assert all(
                row["instance_id"] == snap.id
                for row in client.get("/api/v1/alerts/events", params=query).json()["data"]
            )
        assert len(client.get("/api/v1/metrics/workflows").json()["data"]) == 2
        assert client.get("/api/v1/metrics/overview").json()["monitored_workflow_count"] == 2
        assert client.get("/api/v1/system/status").json()["enabled_n8n_count"] == 2
        assert client.get("/api/v1/metrics/overview").json()["monitoring"]["enabled"] is True
        first_incident = client.get(
            "/api/v1/incidents", params={"instance_id": store.first.id}
        ).json()["data"][0]
        path = f"/api/v1/settings/n8n-instances/{store.first.id}"
        revision = store.first.revision
        for change in (
            {"name": "Renamed"},
            {"base_url": "https://8.8.8.8/proxy", "confirm_same_server": True},
            {"enabled": False},
        ):
            response = client.patch(path, json={"expected_revision": revision, **change})
            assert response.status_code == 200, response.text
            revision = response.json()["revision"]
            assert (
                client.get("/api/v1/incidents", params={"instance_id": store.first.id}).json()[
                    "data"
                ][0]["id"]
                == first_incident["id"]
            )
        assert client.delete(path, headers={"If-Match": str(revision)}).status_code == 200
        assert app.state.registry.get(store.first.workspace_id, store.first.id) is None
        assert (
            client.get("/api/v1/incidents", params={"instance_id": store.first.id}).json()["data"][
                0
            ]["id"]
            == first_incident["id"]
        )
        assert (
            client.get("/api/v1/metrics/workflows", params={"instance_id": store.first.id}).json()[
                "data"
            ][0]["instance_id"]
            == store.first.id
        )
        assert client.get("/api/v1/alerts/events", params={"instance_id": store.first.id}).json()[
            "data"
        ]
        assert (
            client.post(f"/api/v1/incidents/{first_incident['id']}/diagnose").json()[
                "diagnosis_provider"
            ]
            == "mock"
        )
        assert (
            client.post(
                "/api/v1/monitoring/sync", params={"instance_id": store.first.id}
            ).status_code
            == 404
        )
        assert (
            client.get("/api/v1/workflows", params={"instance_id": store.first.id}).status_code
            == 404
        )
        for path in (
            "n8n/status",
            "workflows",
            "executions/failed",
            "workflows/same-workflow/health",
        ):
            assert client.get(f"/api/v1/{path}").status_code == 422
            assert (
                client.get(f"/api/v1/{path}", params={"instance_id": "invalid"}).status_code == 422
            )
        # Another workspace's well-formed ID is rejected before DNS or HTTP.
        wid = "11111111-1111-1111-1111-111111111111"
        with store.factory() as db:
            db.add(Workspace(id=wid, name="Other workspace"))
            db.commit()
        foreign = provision(store.factory, workspace_id=wid)
        before = len(requests)

        async def forbidden_dns(*args, **kwargs):
            raise AssertionError("Foreign instance must not cause DNS")

        monkeypatch.setattr(app.state.outbound.policy, "validate", forbidden_dns)
        for path in (
            "n8n/status",
            "workflows",
            "executions/failed",
            "incidents",
            "metrics/overview",
            "metrics/workflows",
            "alerts/events",
            "monitoring/status",
        ):
            assert (
                client.get(f"/api/v1/{path}", params={"instance_id": foreign.id}).status_code == 404
            )
        assert (
            client.post("/api/v1/monitoring/sync", params={"instance_id": foreign.id}).status_code
            == 404
        )
        assert len(requests) == before


def test_ai_revision_change_is_atomic_and_next_diagnosis_uses_new_snapshot(store):
    import json
    import threading

    from app.ai.providers import MockDiagnosisProvider
    from app.services.incidents import normalize

    ai = provision(
        store.factory,
        kind="ai",
        base_url="https://8.8.8.8/old",
        api_key="old-ai-key",
        model="old-model",
    )
    with store.scoped() as db:
        from app.repositories.incidents import IncidentRepository

        incident, _ = IncidentRepository(db, store.first.workspace_id, store.first.id).create_once(
            normalize(failure())
        )
        db.commit()
        incident_id = incident.id
    entered, release = threading.Event(), threading.Event()
    observed = []
    diagnosis = asyncio.run(MockDiagnosisProvider().diagnose(normalize(failure()))).model_dump()
    diagnosis["summary"] = "Check alpha-key and beta-key and new-ai-key"

    async def handler(request):
        observed.append(
            (
                str(request.url),
                request.headers["Authorization"],
                json.loads(request.content)["model"],
            )
        )
        if len(observed) == 1:
            entered.set()
            await asyncio.to_thread(release.wait, 3)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(diagnosis)}}]}
        )

    app = create_app(store.settings, ai_transport=httpx.MockTransport(handler))
    with OperatorClient(app) as client:
        path = f"/api/v1/incidents/{incident_id}/diagnose"

        # Execute one request on the app loop while a separate DB session rotates configuration.
        async def diagnose_request():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                cookies=client.cookies,
                headers=dict(client.headers),
            ) as http:
                return await http.post(path)

        pending = client.portal.start_task_soon(diagnose_request)
        assert entered.wait(2)
        with store.factory() as db:
            repo = ConnectionRepository(db, LEGACY_WORKSPACE_ID, CIPHER)
            repo.update(
                "ai",
                ai.id,
                ai.revision,
                {"base_url": "https://8.8.8.8/new", "model": "new-model", "api_key": "new-ai-key"},
            )
            db.commit()
        release.set()
        response = pending.result(3)
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "connection_changed"
        assert client.get(f"/api/v1/incidents/{incident_id}").json()["diagnosis"] is None
        response = client.post(path)
        assert response.status_code == 200, response.text
        assert observed == [
            ("https://8.8.8.8/old/chat/completions", "Bearer old-ai-key", "old-model"),
            ("https://8.8.8.8/new/chat/completions", "Bearer new-ai-key", "new-model"),
        ]
        assert all(
            secret not in response.text for secret in ("alpha-key", "beta-key", "new-ai-key")
        )


def test_registry_drains_manual_work_and_reloads_committed_revisions(store):

    async def run():
        entered = asyncio.Event()

        class Blocked:
            async def recent_executions(self, limit, cursor):
                entered.set()
                await asyncio.Event().wait()

        built = []

        def build(snap, slots):
            service = worker(store, snap, Blocked(), slots, alerts=False)
            built.append(service)
            return service

        registry = ConnectionRegistry(lambda: read_enabled_snapshots(store.factory, CIPHER), build)
        assert registry.reconcile_seconds == 5
        await registry.start()
        try:
            service = registry.get(store.first.workspace_id, store.first.id)
            manual = asyncio.create_task(service.sync())
            await entered.wait()
            with store.factory() as db:
                row = db.get(N8nInstance, store.first.id)
                row.enabled, row.revision = False, row.revision + 1
                db.commit()
            await registry.drain(store.first.workspace_id, store.first.id)
            assert manual.cancelled()
            assert service._task is None
            assert registry.get(store.first.workspace_id, store.first.id) is None
            with store.factory() as db:
                row = db.get(N8nInstance, store.second.id)
                row.revision += 1
                db.commit()
            # Periodic reconciliation sees a second process's committed update.
            await registry.reconcile()
            current = registry.get(store.second.workspace_id, store.second.id)
            assert current.snapshot_revision == store.second.revision + 1
            assert all(old._task is None for old in built if old is not current)
        finally:
            await registry.stop()
        assert registry.workers == {}
        assert all(service._task is None for service in built)

    asyncio.run(run())


def test_failed_worker_stop_does_not_start_a_replacement(store):
    from dataclasses import replace

    async def run():
        snapshots = [store.first]
        calls = []

        class Worker:
            async def start(self):
                calls.append("start")

            async def stop(self):
                raise RuntimeError("stop failed")

        registry = ConnectionRegistry(lambda: snapshots, lambda *args: Worker())
        await registry.reconcile()
        original = registry.get(store.first.workspace_id, store.first.id)
        snapshots[0] = replace(store.first, revision=2)
        await registry.reconcile()
        assert registry.get(store.first.workspace_id, store.first.id) is original
        assert calls == ["start"]

    asyncio.run(run())


def test_waiting_for_shared_slots_does_not_hold_a_lease_or_use_a_stale_key(store):
    async def run():
        calls = []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: calls.append(request))
        ) as http:
            slots = asyncio.Semaphore(1)
            await slots.acquire()
            service = worker(
                store,
                store.first,
                N8nClient(store.first, outbound(http), revision_guard(store.factory, store.first)),
                slots,
                alerts=False,
            )
            task = asyncio.create_task(service.sync())
            await asyncio.sleep(0)
            with store.scoped() as db:
                assert (
                    MonitoringRepository(
                        db, store.first.workspace_id, store.first.id
                    ).existing_checkpoint("n8n")
                    is None
                )
            with store.factory() as db:
                row = db.get(N8nInstance, store.first.id)
                row.revision += 1
                db.commit()
            slots.release()
            with pytest.raises(ServiceError) as error:
                await task
            assert error.value.code == "connection_changed"
            assert calls == []

    asyncio.run(run())


def test_retention_is_limited_to_the_owning_instance(store):
    from app.repositories.monitoring import ExecutionHistoryRepository

    now = datetime.now(UTC)
    with store.scoped() as db:
        for snap in (store.first, store.second):
            ExecutionHistoryRepository(db, snap.workspace_id, snap.id).create_once(
                source="n8n",
                workflow_id="same-workflow",
                workflow_name="Old workflow",
                execution_id="old-execution",
                execution_status="success",
                recorded_at=now - timedelta(days=31),
                is_success=True,
            )
        db.commit()
    service = worker(store, store.first, None, alerts=False)
    assert service._acquire_lease()
    try:
        service._run_retention_if_due()
    finally:
        service._release_lease()
    histories = records(store, ExecutionHistory)
    assert len(histories) == 1
    assert histories[0].instance_id == store.second.id


def test_configuration_error_is_safe_and_restart_ignores_environment_credentials(store):
    from pydantic import SecretStr

    with store.factory() as db:
        row = db.get(N8nInstance, store.first.id)
        row.api_key_ciphertext = "invalid-ciphertext"
        db.commit()
    app = create_app(store.settings)
    with OperatorClient(app) as client:
        response = client.get("/api/v1/system/status")
        states = {row["instance_id"]: row["state"] for row in response.json()["n8n_instances"]}
        assert states[store.first.id] == "configuration_error"
        assert states[store.second.id] == "ready"
        assert "ciphertext" not in response.text
        assert "alpha-key" not in response.text
        assert app.state.registry.get(store.first.workspace_id, store.first.id) is None
    with store.factory() as db:
        for snap in (store.first, store.second):
            row = db.get(N8nInstance, snap.id)
            row.enabled = False
            row.revision += 1
        db.commit()
    settings = store.settings.model_copy(
        update={
            "n8n_base_url": "https://8.8.8.8",
            "n8n_api_key": SecretStr("ignored-environment-key"),
        }
    )
    with OperatorClient(create_app(settings)) as client:
        assert client.app.state.registry.workers == {}
        assert not hasattr(client.app.state, "n8n")
        assert client.get("/api/v1/system/status").json()["enabled_n8n_count"] == 0


def test_periodic_registry_reload_observes_a_remote_commit_without_notification(store):
    async def run():
        reloaded = asyncio.Event()
        stopped = []

        class Worker:
            def __init__(self, snapshot):
                self.snapshot = snapshot

            async def start(self):
                if self.snapshot.revision > 1:
                    reloaded.set()

            async def stop(self):
                stopped.append(self.snapshot.id)

        registry = ConnectionRegistry(
            lambda: read_enabled_snapshots(store.factory, CIPHER),
            lambda snapshot, slots: Worker(snapshot),
        )
        assert registry.reconcile_seconds == 5
        registry.reconcile_seconds = 0.01  # Exercise the same timer with a short test interval.
        await registry.start()
        try:
            with store.factory() as db:
                row = db.get(N8nInstance, store.first.id)
                row.revision += 1
                db.commit()
            await asyncio.wait_for(reloaded.wait(), 1)
            assert store.first.id in stopped
            assert registry.get(store.first.workspace_id, store.first.id).snapshot.revision == 2
        finally:
            await registry.stop()

    asyncio.run(run())
