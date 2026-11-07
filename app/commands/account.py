"""Local password recovery, disablement, and session revocation."""

import argparse
import getpass

from sqlalchemy import select

from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import upgrade_database
from app.models.auth import User
from app.services.auth import AuthContext, audit, passwords, revoke_user_sessions


def operate(factory, email, action, password=None):
    with factory() as session:
        user = session.scalar(select(User).where(User.email == email.strip().casefold()))
        if user is None:
            raise ValueError("Account was not found")
        if action == "reset":
            if password is None or not 12 <= len(password) <= 128:
                raise ValueError("Password must contain 12 to 128 characters")
            user.password_hash = passwords.hash(password)
        elif action == "disable":
            user.disabled = True
        elif action != "revoke":
            raise ValueError("Unknown operation")
        revoke_user_sessions(session, user.id)
        # CLI is an operator action; workspace is intentionally absent.
        context = AuthContext(user.id, None, "admin")
        audit(session, context, f"operator.{action}")
        session.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["reset", "disable", "revoke"])
    parser.add_argument("email")
    args = parser.parse_args()
    password = None
    if args.action == "reset":
        password = getpass.getpass("New password (12+ characters): ")
        if password != getpass.getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
    config = Settings()
    upgrade_database(config.database_url)
    engine, factory = create_database(config.database_url)
    try:
        operate(factory, args.email, args.action, password)
        print("Account updated and all sessions revoked.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
