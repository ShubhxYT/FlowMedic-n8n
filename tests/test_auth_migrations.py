from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.commands.bootstrap_owner import bootstrap
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.models.auth import LEGACY_WORKSPACE_ID, User, Workspace, WorkspaceMembership

PASSWORD = "test-password-123456"

TABLES = (
    "incidents",
    "execution_history",
    "monitoring_checkpoints",
    "alert_rules",
    "alert_events",
    "alert_delivery_attempts",
    "alert_condition_states",
)


def old_schema(url):
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0003_alerting_foundation")


def snapshot(engine):
    with engine.connect() as connection:
        metadata = MetaData()
        return {
            name: [
                dict(row)
                for row in connection.execute(
                    select(Table(name, metadata, autoload_with=connection))
                ).mappings()
            ]
            for name in TABLES
        }


def test_upgrade_preserves_every_existing_value_and_relation(tmp_path):
    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    old_schema(url)
    engine, factory = create_database(url)
    metadata = MetaData()
    now = datetime.now(UTC)
    with engine.begin() as connection:
        tables = {name: Table(name, metadata, autoload_with=connection) for name in TABLES}
        connection.execute(
            tables["incidents"]
            .insert()
            .values(
                id="incident",
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
            tables["execution_history"]
            .insert()
            .values(
                id="history",
                source="n8n",
                workflow_id="workflow",
                workflow_name="Keep",
                execution_id="execution",
                recorded_at=now,
                is_success=False,
                incident_id="incident",
            )
        )
        connection.execute(
            tables["monitoring_checkpoints"]
            .insert()
            .values(
                id="checkpoint",
                source="n8n",
                source_identifier="default",
                created_at=now,
                updated_at=now,
                consecutive_failure_count=2,
                cursor_value="cursor",
                backfill_cursor="backfill",
                lease_owner_id="lease-owner",
                lease_expires_at=now + timedelta(minutes=5),
            )
        )
        connection.execute(
            tables["alert_rules"]
            .insert()
            .values(
                id="rule",
                name="same",
                trigger_type="new_incident",
                enabled=True,
                cooldown_seconds=300,
                delivery_provider="mock",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            tables["alert_events"]
            .insert()
            .values(
                id="event",
                rule_id="rule",
                trigger_type="new_incident",
                event_key="key",
                deduplication_key="dedup",
                incident_id="incident",
                status="delivered",
                cooldown_until=now,
                created_at=now,
            )
        )
        connection.execute(
            tables["alert_delivery_attempts"]
            .insert()
            .values(
                id="attempt",
                alert_event_id="event",
                provider="mock",
                attempt_number=1,
                status="delivered",
                started_at=now,
                completed_at=now,
            )
        )
        connection.execute(
            tables["alert_condition_states"]
            .insert()
            .values(
                id="condition",
                condition_type="unhealthy",
                scope_key="workflow",
                current_state="unhealthy",
                updated_at=now,
            )
        )
    # Prior create_all installations used this unique index in addition to a constraint.
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX ix_incidents_execution_id")
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX ix_incidents_execution_id ON incidents(execution_id)"
        )
    before = snapshot(engine)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0004_workspace_auth")
    after = snapshot(engine)
    for name, rows in before.items():
        assert len(after[name]) == len(rows)
        for previous, current in zip(rows, after[name], strict=True):
            for key, value in previous.items():
                assert current[key] == value, (name, key)
            if name != "alert_delivery_attempts":
                assert current["workspace_id"] == LEGACY_WORKSPACE_ID
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    with factory() as session:
        session.add(Workspace(id="other", name="Other"))
        session.commit()
    with engine.begin() as connection:
        table = Table("incidents", MetaData(), autoload_with=connection)
        row = dict(after["incidents"][0])
        row.update(id="other-incident", workspace_id="other")
        connection.execute(table.insert().values(**row))
        row["id"] = "duplicate-in-other"
        with pytest.raises(IntegrityError):
            connection.execute(table.insert().values(**row))
    owner = bootstrap(factory, "operator@example.com", PASSWORD)
    assert bootstrap(factory, "operator@example.com", "a-different-password") == owner
    with pytest.raises(ValueError):
        bootstrap(factory, "other@example.com", PASSWORD)
    with factory() as session:
        assert session.get(WorkspaceMembership, (owner, LEGACY_WORKSPACE_ID)).role == "admin"
        session.add(User(email="operator@example.com", password_hash="not-used"))
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()


def test_fresh_migration_and_public_user_cannot_bootstrap_legacy(tmp_path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    upgrade_database(url)
    engine, factory = create_database(url)
    assert {"users", "sessions", "audit_events", "auth_throttles"} <= set(
        inspect(engine).get_table_names()
    )
    with factory() as db:
        db.add(User(email="public@example.com", password_hash="not-used"))
        db.commit()
    with pytest.raises(ValueError, match="public users"):
        bootstrap(factory, "public@example.com", PASSWORD)
    engine.dispose()
