"""Alembic environment. The app passes its open SQLite connection in config.attributes["connection"]
(Store._migrate); the CLI (`mise run db:revision`) uses sqlalchemy.url from alembic.ini."""

from alembic import context
from sqlalchemy import create_engine

config = context.config
connection = config.attributes.get("connection")

if connection is not None:
    context.configure(connection=connection, target_metadata=None, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=None, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
