from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def create_database(url):
    options = {}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 15}
        if ":memory:" in url:
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_foreign_keys(connection, record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.close()

    return engine, sessionmaker(engine, expire_on_commit=False)
