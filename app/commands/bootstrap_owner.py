"""Explicit local owner provisioning. No command accepts a password argument."""

import argparse
import getpass

from pwdlib import PasswordHash
from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.models.audit import AuditEvent
from app.models.auth import (
    LEGACY_WORKSPACE_ID,
    OperatorState,
    User,
    Workspace,
    WorkspaceMembership,
)

passwords = PasswordHash.recommended()


def bootstrap(factory, email, password):
    email = email.strip().casefold()
    if (
        not 3 <= len(email) <= 254
        or email.count("@") != 1
        or any(c.isspace() for c in email)
        or not email.split("@")[-1].count(".")
        or email.startswith("@")
        or email.endswith(".")
    ):
        raise ValueError("A valid operator email is required")
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12 to 128 characters")
    with factory() as session:
        state = session.get(OperatorState, "legacy")
        if state:
            owner = session.get(User, state.owner_user_id)
            if owner.email != email:
                raise ValueError("A different legacy owner is already provisioned")
            return owner.id
        if session.scalar(select(User).where(User.email == email)):
            raise ValueError("Use a new operator identity; public users cannot claim legacy data")
        if session.get(Workspace, LEGACY_WORKSPACE_ID) is None:
            session.add(Workspace(id=LEGACY_WORKSPACE_ID, name="Legacy operator"))
            session.flush()
        user = User(email=email, password_hash=passwords.hash(password))
        session.add(user)
        session.flush()
        session.add(
            WorkspaceMembership(user_id=user.id, workspace_id=LEGACY_WORKSPACE_ID, role="admin")
        )
        session.add(OperatorState(id="legacy", owner_user_id=user.id))
        session.add(
            AuditEvent(
                actor_id=user.id,
                workspace_id=LEGACY_WORKSPACE_ID,
                action="owner.bootstrap",
                outcome="success",
            )
        )
        session.commit()
        return user.id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()
    config = Settings()
    upgrade_database(config.database_url)
    engine, factory = create_database(config.database_url)
    try:
        password = getpass.getpass("Owner password (12+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
        bootstrap(factory, args.email, password)
        print("Legacy owner provisioned; existing credentials were not changed on repeat runs.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
