from alembic import context
from sqlalchemy import create_engine

from legal_ai.config import load_settings


def run_migrations_online() -> None:
    engine = create_engine(load_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
