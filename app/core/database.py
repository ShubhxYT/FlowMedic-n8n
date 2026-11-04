from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def create_database(url):
    options = {}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            options["poolclass"] = StaticPool
    engine = create_engine(url, **options)
    return engine, sessionmaker(engine, expire_on_commit=False)
