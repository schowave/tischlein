"""Familien-Funktionen: freie Tage, Bewertungen, Einkaufsliste, Ideen, Tage ohne Mittagessen im Plan."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("meals")}
    if "skipped" not in columns:
        op.execute("ALTER TABLE meals ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0")
    op.execute("""CREATE TABLE IF NOT EXISTS skipdays (
        day TEXT PRIMARY KEY,
        reason TEXT NOT NULL DEFAULT '')""")
    op.execute("""CREATE TABLE IF NOT EXISTS ratings (
        recipe_uid TEXT NOT NULL,
        day TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK (verdict IN ('gut', 'mittel', 'schlecht')),
        person TEXT NOT NULL DEFAULT '',
        at TEXT NOT NULL,
        PRIMARY KEY (recipe_uid, day))""")
    op.execute("""CREATE TABLE IF NOT EXISTS shopping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT NOT NULL,
        name TEXT NOT NULL,
        amount TEXT NOT NULL DEFAULT '',
        grp TEXT NOT NULL DEFAULT 'Sonstiges',
        recipes TEXT NOT NULL DEFAULT '',
        manual INTEGER NOT NULL DEFAULT 0,
        done INTEGER NOT NULL DEFAULT 0,
        done_by TEXT NOT NULL DEFAULT '',
        position INTEGER NOT NULL DEFAULT 0)""")
    op.execute("""CREATE TABLE IF NOT EXISTS ideas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT NOT NULL,
        data TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'neu' CHECK (status IN ('neu', 'uebernommen', 'verworfen')),
        paprika_uid TEXT NOT NULL DEFAULT '')""")


def downgrade() -> None:
    for table in ("ideas", "shopping", "ratings", "skipdays"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    with op.batch_alter_table("meals") as batch:
        batch.drop_column("skipped")
