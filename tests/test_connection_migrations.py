import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.commands.bootstrap_owner import bootstrap
from app.commands.import_connections import import_connections
from app.core.config import Settings
from app.core.database import create_database
from app.core.encryption import ConnectionCipher
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.models.auth import LEGACY_WORKSPACE_ID
from app.repositories.connections import (
    ConnectionRepository,
    legacy_connection_id,
    verify_keyring_startup,
)

PASSWORD = "test-password-123456"
PERSONAL = "11111111-1111-1111-1111-111111111111"
EMPTY = "22222222-2222-2222-2222-222222222222"
N8N_KEY = "fixture-n8n-secret-abc123"
AI_KEY = "fixture-ai-secret-def456"
TABLES = (
    "incidents",
    "execution_history",
    "monitoring_checkpoints",
    "alert_rules",
    "alert_events",
    "alert_delivery_attempts",
    "alert_condition_states",
)
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def old_schema(url):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0004_workspace_auth")


def snapshot(engine):
    with engine.connect() as connection:
        metadata = MetaData()
        tables = {
            name: Table(name, metadata, autoload_with=connection) for name in TABLES
        }
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    select(table).order_by(table.c.id)
                ).mappings()
            ]
            for name, table in tables.items()
        }


def seed(engine):
    with engine.begin() as connection:
        metadata = MetaData()
        tables = {name: Table(name, metadata, autoload_with=connection) for name in TABLES}
        connection.execute(
            tables["incidents"].insert().values(
                id="incident-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                execution_id="execution",
                workflow_id="workflow",
                workflow_name="Keep",
                error_type="Error",
                error_message="sanitized",
                status="diagnosed",
                severity="medium",
                diagnosis={"summary": "keep diagnosis"},
                diagnosis_provider="mock",
            )
        )
        connection.execute(
            tables["execution_history"].insert().values(
                id="history-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                source="n8n",
                workflow_id="workflow",
                workflow_name="Keep",
                execution_id="execution",
                recorded_at=NOW,
                is_success=False,
                incident_id="incident-a",
            )
        )
        connection.execute(
            tables["monitoring_checkpoints"].insert().values(
                id="checkpoint-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                source="n8n",
                source_identifier="default",
                created_at=NOW,
                updated_at=NOW,
                consecutive_failure_count=2,
                cursor_value="cursor",
                backfill_cursor="backfill",
                backfill_completed_at=None,
                last_fresh_poll_at=NOW,
                lease_owner_id="old-owner",
                lease_acquired_at=NOW,
                lease_heartbeat_at=NOW,
                lease_expires_at=NOW + timedelta(minutes=5),
            )
        )
        connection.execute(
            tables["alert_rules"].insert().values(
                id="rule-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                name="same",
                trigger_type="new_incident",
                enabled=True,
                cooldown_seconds=300,
                delivery_provider="mock",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            tables["alert_events"].insert().values(
                id="event-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                rule_id="rule-a",
                trigger_type="new_incident",
                event_key="key",
                deduplication_key="dedup",
                incident_id="incident-a",
                status="delivered",
                cooldown_until=NOW,
                created_at=NOW,
            )
        )
        connection.execute(
            tables["alert_delivery_attempts"].insert().values(
                id="attempt-a",
                alert_event_id="event-a",
                provider="mock",
                attempt_number=1,
                status="delivered",
                started_at=NOW,
                completed_at=NOW,
            )
        )
        connection.execute(
            tables["alert_condition_states"].insert().values(
                id="condition-a",
                workspace_id=LEGACY_WORKSPACE_ID,
                condition_type="unhealthy",
                scope_key="workflow",
                current_state="unhealthy",
                updated_at=NOW,
            )
        )
        # A second workspace with history receives its own disabled placeholder.
        connection.execute(
            tables["incidents"].insert().values(
                id="incident-b",
                workspace_id=PERSONAL,
                execution_id="execution",
                workflow_id="workflow",
                workflow_name="Personal",
                error_type="Error",
                error_message="sanitized",
                status="open",
                severity="low",
            )
        )
        connection.execute(
            tables["alert_condition_states"].insert().values(
                id="condition-b",
                workspace_id=PERSONAL,
                condition_type="unhealthy",
                scope_key="workflow",
                current_state="healthy",
                updated_at=NOW,
            )
        )


def test_upgrade_preserves_history_and_scopes_every_identity(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    old_schema(url)
    engine, factory = create_database(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            (PERSONAL, "Personal", 1),
        )
        connection.exec_driver_sql(
            "INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
            (EMPTY, "Empty", 1),
        )
    bootstrap(factory, "operator@example.com", PASSWORD)
    seed(engine)
    before = snapshot(engine)

    upgrade_database(url)

    after = snapshot(engine)
    for name, rows in before.items():
        assert len(after[name]) == len(rows)
        for previous, current in zip(rows, after[name], strict=True):
            for key, value in previous.items():
                if name == "monitoring_checkpoints" and key.startswith("lease_"):
                    continue
                assert current[key] == value, (name, key)

    with engine.connect() as connection:
        instances = connection.execute(
            select(
                Table("n8n_instances", MetaData(), autoload_with=connection)
            )
        ).mappings().all()
        by_workspace = {row["workspace_id"]: row for row in instances}
        assert set(by_workspace) == {LEGACY_WORKSPACE_ID, PERSONAL}
        for workspace_id, row in by_workspace.items():
            assert row["id"] == legacy_connection_id(workspace_id, "n8n")
            assert row["enabled"] is False or not row["enabled"]
            assert row["api_key_ciphertext"] is None
            assert row["base_url"] == "https://legacy.invalid"
            assert row["import_tag"] == "legacy-env-v1"
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []

    with engine.connect() as connection:
        for name in (
            "incidents",
            "execution_history",
            "alert_events",
            "alert_condition_states",
        ):
            rows = connection.execute(
                select(Table(name, MetaData(), autoload_with=connection))
            ).mappings()
            for row in rows:
                if row["workspace_id"] in by_workspace:
                    assert row["instance_id"] == legacy_connection_id(row["workspace_id"], "n8n")
        checkpoints = connection.execute(
            select(Table("monitoring_checkpoints", MetaData(), autoload_with=connection))
        ).mappings().all()
        assert len(checkpoints) == 1
        assert checkpoints[0]["backfill_cursor"] == "backfill"
        assert checkpoints[0]["cursor_value"] == "cursor"
        assert checkpoints[0]["last_fresh_poll_at"] is not None
        for field in (
            "lease_owner_id",
            "lease_acquired_at",
            "lease_heartbeat_at",
            "lease_expires_at",
        ):
            assert checkpoints[0][field] is None

    # The same external execution ID is valid on a second instance in one workspace.
    with engine.begin() as connection:
        instances = Table("n8n_instances", MetaData(), autoload_with=connection)
        connection.execute(
            instances.insert().values(
                id="second-instance",
                workspace_id=LEGACY_WORKSPACE_ID,
                name="Second",
                base_url="https://second.example.test",
                enabled=False,
                deleted=False,
                revision=1,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        incidents = Table("incidents", MetaData(), autoload_with=connection)
        row = dict(before["incidents"][0])
        row.update(id="incident-second", instance_id="second-instance")
        connection.execute(incidents.insert().values(**row))
        row["id"] = "incident-duplicate"
        with pytest.raises(IntegrityError):
            connection.execute(incidents.insert().values(**row))
        row = dict(before["incidents"][1])
        row.update(
            id="incident-cross",
            workspace_id=EMPTY,
            instance_id=legacy_connection_id(LEGACY_WORKSPACE_ID, "n8n"),
        )
        with pytest.raises(IntegrityError):
            connection.execute(incidents.insert().values(**row))

    assert "users" in inspect(engine).get_table_names()
    engine.dispose()


def test_import_is_explicit_idempotent_and_never_resurrects(tmp_path, caplog):
    url = f"sqlite:///{tmp_path / 'import.db'}"
    old_schema(url)
    engine, factory = create_database(url)
    bootstrap(factory, "operator@example.com", PASSWORD)
    upgrade_database(url)

    keyring = json.dumps({"one": Fernet.generate_key().decode()})
    settings = Settings(
        _env_file=None,
        database_url=url,
        connection_encryption_keys=keyring,
        connection_active_key_id="one",
        n8n_base_url="https://n8n.example.test",
        n8n_api_key=N8N_KEY,
        ai_api_key=AI_KEY,
        ai_base_url="https://api.example.test/v1",
        ai_model="probe-model",
    )
    result = import_connections(factory, settings, LEGACY_WORKSPACE_ID)
    assert result["created"] == 1 and result["imported"] == 1
    assert N8N_KEY not in caplog.text and AI_KEY not in caplog.text

    again = import_connections(factory, settings, LEGACY_WORKSPACE_ID)
    assert again["created"] == 0 and again["imported"] == 0 and again["skipped"] == 2

    cipher = ConnectionCipher(keyring, "one")
    engine.dispose()
    engine, factory = create_database(url)
    verify_keyring_startup(factory, cipher)
    with factory() as session:
        repository = ConnectionRepository(session, LEGACY_WORKSPACE_ID, cipher)
        provider = repository.legacy("ai")
        provider.enabled = True
        assert repository.legacy("n8n").api_key_ciphertext
        snapshot_value = repository.snapshot("ai", provider.id)
        assert AI_KEY not in repr(snapshot_value) and AI_KEY not in str(snapshot_value)
        session.commit()

    with factory() as session:
        repository = ConnectionRepository(session, LEGACY_WORKSPACE_ID, cipher)
        instrument = repository.legacy("n8n")
        repository.soft_delete("n8n", instrument.id, instrument.revision)
        session.commit()
    tombstone = import_connections(factory, settings, LEGACY_WORKSPACE_ID)
    assert tombstone["skipped"] == 2
    replaced = import_connections(
        factory, settings, LEGACY_WORKSPACE_ID, replace_existing=True
    )
    assert replaced["skipped"] == 1 and replaced["imported"] == 1

    with engine.connect() as connection:
        rows = connection.execute(
            select(Table("n8n_instances", MetaData(), autoload_with=connection))
        ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["deleted"] in (1, True)
        assert rows[0]["api_key_ciphertext"] is None
    import sqlite3

    with sqlite3.connect(tmp_path / "import.db") as raw:
        dump = "\n".join(raw.iterdump())
    assert N8N_KEY not in dump and AI_KEY not in dump

    # A different workspace cannot be imported into.
    with pytest.raises(ValueError):
        import_connections(factory, settings, PERSONAL)

    engine.dispose()


def test_wrong_key_fails_closed_without_modifying_ciphertext(tmp_path):
    url = f"sqlite:///{tmp_path / 'wrong.db'}"
    old_schema(url)
    engine, factory = create_database(url)
    bootstrap(factory, "operator@example.com", PASSWORD)
    upgrade_database(url)
    keyring = json.dumps({"one": Fernet.generate_key().decode()})
    settings = Settings(
        _env_file=None,
        database_url=url,
        connection_encryption_keys=keyring,
        connection_active_key_id="one",
        n8n_base_url="https://n8n.example.test",
        n8n_api_key=N8N_KEY,
    )
    import_connections(factory, settings, LEGACY_WORKSPACE_ID)
    cipher = ConnectionCipher(keyring, "one")
    with factory() as session:
        repository = ConnectionRepository(session, LEGACY_WORKSPACE_ID, cipher)
        row = repository.legacy("n8n")
        repository.update("n8n", row.id, row.revision, {"enabled": True})
        session.commit()
    with factory() as session:
        row = ConnectionRepository(session, LEGACY_WORKSPACE_ID, cipher).get("n8n", row.id)
        revision = row.revision
        ciphertext = row.api_key_ciphertext
    engine.dispose()

    copy = tmp_path / "wrong-copy.db"
    shutil.copy(tmp_path / "wrong.db", copy)
    engine, factory = create_database(f"sqlite:///{copy}")
    wrong = ConnectionCipher(json.dumps({"one": Fernet.generate_key().decode()}), "one")
    with pytest.raises(ServiceError) as read_error:
        verify_keyring_startup(factory, wrong)
    assert read_error.value.code == "connection_encryption_unavailable"
    with factory() as session:
        repository = ConnectionRepository(session, LEGACY_WORKSPACE_ID, wrong)
        with pytest.raises(ServiceError):
            repository.snapshot("n8n", row.id)
        with pytest.raises(ServiceError):
            repository.update(
                "n8n", row.id, revision, {"api_key": "replacement-secret-xyz"}
            )
        with pytest.raises(ServiceError):
            repository.update("n8n", row.id, revision, {"remove_api_key": True})
        with pytest.raises(ServiceError):
            repository.soft_delete("n8n", row.id, revision)
        session.rollback()
    with engine.connect() as connection:
        stored = connection.execute(
            select(Table("n8n_instances", MetaData(), autoload_with=connection))
        ).mappings().one()
        assert stored["api_key_ciphertext"] == ciphertext
        assert not stored["deleted"]
    engine.dispose()
