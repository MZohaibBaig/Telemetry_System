from sqlalchemy import text

from app.database import Base, engine
from app import models  # noqa: F401  ensures all tables are registered on Base.metadata

# Arbitrary constant; serialises concurrent startups so two processes don't
# race on CREATE TABLE / create_hypertable.
_INIT_LOCK_ID = 727001


def init_db() -> None:
    """Create tables and the `readings` hypertable. Idempotent."""
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _INIT_LOCK_ID})
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            conn.commit()
            Base.metadata.create_all(bind=conn)
            conn.execute(
                text(
                    "SELECT create_hypertable("
                    "'readings', 'recorded_at', "
                    "if_not_exists => TRUE, migrate_data => TRUE)"
                )
            )
            # Retention drops whole chunks only. The 7-day default would keep
            # data ~9 days, so use 1-day chunks (applies to new chunks).
            conn.execute(
                text("SELECT set_chunk_time_interval('readings', INTERVAL '1 day')")
            )
            conn.execute(
                text(
                    "SELECT add_retention_policy("
                    "'readings', INTERVAL '48 hours', if_not_exists => TRUE)"
                )
            )
            conn.commit()
        finally:
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _INIT_LOCK_ID})
            conn.commit()
