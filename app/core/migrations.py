from pathlib import Path

from alembic.config import Config

from alembic import command


def upgrade_database(database_url: str) -> None:
    """Run the non-destructive local schema upgrade before opening production storage."""
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
