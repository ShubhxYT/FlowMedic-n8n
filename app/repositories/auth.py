import hashlib
import time

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert

from app.core.errors import ServiceError
from app.models.auth import AuthThrottle, User


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class AuthRepository:
    def __init__(self, session):
        self.session = session

    def user_by_email(self, email):
        return self.session.scalar(select(User).where(User.email == email))

    def throttle(self, keys):
        # One SQLite write transaction serializes capacity checks and atomic upserts.
        now = int(time.time())
        window = now // 900 * 900
        self.session.execute(delete(AuthThrottle).where(AuthThrottle.window_start < window))
        for raw_key, limit in keys:
            key = digest(raw_key)
            exists = self.session.get(AuthThrottle, key)
            if exists is None:
                size = self.session.scalar(select(func.count()).select_from(AuthThrottle))
                if size >= 10000:
                    self.session.commit()
                    raise ServiceError("rate_limited", "Please try again later", 429)
            statement = insert(AuthThrottle).values(key=key, window_start=window, count=1)
            self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=[AuthThrottle.key],
                    set_={"count": AuthThrottle.count + 1, "window_start": window},
                )
            )
            row = self.session.get(AuthThrottle, key, populate_existing=True)
            if row.count > limit:
                self.session.commit()
                raise ServiceError("rate_limited", "Please try again later", 429)
        # Failed credentials also consume their durable budget.
        self.session.commit()
