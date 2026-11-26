"""Provision a shared demo login with its own private workspace.

The demo password is chosen by the operator and may be shorter than the public signup
policy. It is still stored only as an Argon2 hash. Existing accounts are never modified.
"""

import argparse
import getpass
import os

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.models.audit import AuditEvent
from app.models.auth import User, Workspace, WorkspaceMembership
from app.schemas.auth import normalize_email
from app.services.auth import passwords


def ensure_demo_account(factory, email, password):
    """Create the account if missing. Returns (user_id, created)."""
    email = normalize_email(email)
    if not 1 <= len(password) <= 128:
        raise ValueError("Demo password must contain 1 to 128 characters")
    with factory() as session:
        existing = session.scalar(select(User).where(User.email == email))
        if existing is not None:
            return existing.id, False
        user = User(email=email, password_hash=passwords.hash(password))
        session.add(user)
        session.flush()
        workspace = Workspace(name="Demo workspace")
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMembership(user_id=user.id, workspace_id=workspace.id, role="admin"))
        session.add(
            AuditEvent(
                actor_id=user.id,
                workspace_id=workspace.id,
                action="operator.demo_account",
                outcome="success",
            )
        )
        session.commit()
        return user.id, True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()
    password = os.environ.get("DEMO_ACCOUNT_PASSWORD") or getpass.getpass("Demo password: ")
    config = Settings()
    upgrade_database(config.database_url)
    engine, factory = create_database(config.database_url)
    try:
        _, created = ensure_demo_account(factory, args.email, password)
        print("Demo account created." if created else "Account already exists; unchanged.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
