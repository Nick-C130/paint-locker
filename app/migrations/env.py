from alembic import context

from app.models import Base

config = context.config
target_metadata = Base.metadata

# Tables managed outside SQLAlchemy models (FTS5 virtual table and its shadow tables).
IGNORE_PREFIXES = ("search_index",)


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table" and name and name.startswith(IGNORE_PREFIXES):
        return False
    return True


def run_migrations_online() -> None:
    engine = config.attributes.get("engine")
    if engine is None:
        from sqlalchemy import create_engine

        engine = create_engine(config.get_main_option("sqlalchemy.url"))
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


run_migrations_online()
