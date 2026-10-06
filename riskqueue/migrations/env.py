from alembic import context
from sqlalchemy import engine_from_config, pool

from riskqueue.db.models import Base
from riskqueue.db.session import normalize_database_url

config = context.config
target_metadata = Base.metadata


def run_migrations_online():
    connectable = config.attributes.get("connection")
    if connectable is not None:
        context.configure(connection=connectable, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return
    import os

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for migrations")
    config.set_main_option("sqlalchemy.url", normalize_database_url(url).replace("%", "%%"))
    engine = engine_from_config(
        config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
