"""Basis: Schema bis Version 0.2.0 (Rezepte, Pläne, Paprika-Cache, Einstellungen, Rückmeldungen).

Datenbanken aus der Zeit vor Alembic werden beim Start auf diese Revision gestempelt.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE IF NOT EXISTS recipes (
        uid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        tags TEXT NOT NULL,
        rating INTEGER NOT NULL DEFAULT 0,
        source_url TEXT NOT NULL DEFAULT '',
        total_time TEXT NOT NULL DEFAULT '',
        imported_at TEXT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS plans (
        week_start TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('entwurf', 'freigegeben')),
        updated_at TEXT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS meals (
        week_start TEXT NOT NULL REFERENCES plans(week_start) ON DELETE CASCADE,
        day TEXT NOT NULL,
        recipe_uid TEXT,
        name TEXT NOT NULL,
        theme TEXT NOT NULL,
        is_new INTEGER NOT NULL DEFAULT 0,
        reason TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (week_start, day))""")
    op.execute("""CREATE TABLE IF NOT EXISTS paprika_cache (
        uid TEXT PRIMARY KEY,
        hash TEXT NOT NULL,
        data TEXT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL)""")
    op.execute("""CREATE TABLE IF NOT EXISTS feedback (
        recipe_uid TEXT PRIMARY KEY,
        verdict TEXT NOT NULL CHECK (verdict IN ('ok', 'nein')),
        at TEXT NOT NULL)""")


def downgrade() -> None:
    for table in ("feedback", "meta", "paprika_cache", "meals", "plans", "recipes"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
