"""Explicit import of legacy environment credentials into managed connections."""

import argparse

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.core.encryption import ConnectionCipher
from app.core.errors import ServiceError
from app.core.migrations import upgrade_database
from app.models.auth import LEGACY_WORKSPACE_ID, WorkspaceMembership
from app.repositories.connections import (
    IMPORT_TAG,
    ConnectionRepository,
    ensure_key_check,
)


def build_cipher(config: Settings) -> ConnectionCipher:
    keys = config.connection_encryption_keys.get_secret_value()
    if not keys:
        raise ServiceError(
            "connection_encryption_unavailable",
            "Connection encryption is unavailable; contact the operator",
            503,
        )
    return ConnectionCipher(keys, config.connection_active_key_id)


def _import_one(
    repository: ConnectionRepository,
    cipher: ConnectionCipher,
    kind: str,
    values: dict,
    replace_existing: bool,
) -> tuple[str, str | None]:
    """Return (action, connection id) where action is created/imported/skipped."""
    row = repository.legacy(kind)
    if row is not None and row.deleted:
        return "skipped", None
    if row is None:
        created = repository.create(kind, dict(values))
        created.import_tag = IMPORT_TAG
        return "created", created.id
    if row.api_key_ciphertext:
        if not replace_existing:
            return "skipped", None
        cipher.decrypt(
            row.api_key_ciphertext,
            workspace_id=repository.workspace_id,
            connection_id=row.id,
        )
    repository.update(
        kind,
        row.id,
        row.revision,
        {key: value for key, value in values.items() if key != "kind"},
    )
    return "imported", row.id


def import_connections(
    factory,
    config: Settings,
    workspace_id: str,
    *,
    replace_existing: bool = False,
) -> dict:
    if workspace_id != LEGACY_WORKSPACE_ID:
        raise ValueError("Import is permitted only for the reserved operator workspace")
    counts = {"created": 0, "imported": 0, "skipped": 0}
    ids: list[str] = []
    with factory() as session:
        try:
            admin = session.scalar(
                select(WorkspaceMembership).where(
                    WorkspaceMembership.workspace_id == workspace_id,
                    WorkspaceMembership.role == "admin",
                )
            )
            if admin is None:
                raise ValueError("Bootstrap a workspace admin before importing connections")
            cipher = build_cipher(config)
            ensure_key_check(session, cipher)
            repository = ConnectionRepository(session, workspace_id, cipher)

            n8n_base = config.n8n_base_url
            n8n_key = config.n8n_api_key.get_secret_value()
            if n8n_base and n8n_key:
                action, connection_id = _import_one(
                    repository,
                    cipher,
                    "n8n",
                    {"name": "Legacy n8n", "base_url": n8n_base, "api_key": n8n_key},
                    replace_existing,
                )
                counts[action] += 1
                if connection_id:
                    ids.append(connection_id)
            else:
                counts["skipped"] += 1

            ai_key = config.ai_api_key.get_secret_value()
            if ai_key:
                action, connection_id = _import_one(
                    repository,
                    cipher,
                    "ai",
                    {
                        "name": "Legacy AI provider",
                        "base_url": config.ai_base_url,
                        "model": config.ai_model,
                        "kind": "openai-compatible",
                        "api_key": ai_key,
                    },
                    replace_existing,
                )
                counts[action] += 1
                if connection_id:
                    ids.append(connection_id)
            else:
                counts["skipped"] += 1

            session.commit()
        except Exception:
            session.rollback()
            raise
    return {**counts, "ids": ids}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    config = Settings()
    try:
        upgrade_database(config.database_url)
        engine, factory = create_database(config.database_url)
        try:
            result = import_connections(
                factory,
                config,
                args.workspace_id,
                replace_existing=args.replace_existing,
            )
        finally:
            engine.dispose()
    except (ValueError, ServiceError) as exc:
        message = exc.message if isinstance(exc, ServiceError) else str(exc)
        print(message)
        raise SystemExit(1) from None
    print(
        f"created={result['created']} imported={result['imported']} "
        f"skipped={result['skipped']} ids={','.join(result['ids'])}"
    )


if __name__ == "__main__":
    main()
