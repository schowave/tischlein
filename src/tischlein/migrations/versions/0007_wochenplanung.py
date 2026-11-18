"""Wochenplanung: Rezept wählen, Reste-Tag, Themenwechsel und Nachträge (Gekochtes ohne Plan)."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # chosen = picked by hand (stays when the week is re-rolled); leftovers = eats the previous dish
    op.execute("ALTER TABLE meals ADD COLUMN chosen INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE meals ADD COLUMN leftovers INTEGER NOT NULL DEFAULT 0")
    # a theme for one day instead of the weekly schedule
    op.execute("CREATE TABLE IF NOT EXISTS themedays (day TEXT PRIMARY KEY, theme TEXT NOT NULL)")
    # what was actually cooked when it was not the plan; one per day, recipe_uid NULL = free text
    op.execute(
        "CREATE TABLE IF NOT EXISTS cooked (day TEXT PRIMARY KEY, recipe_uid TEXT, name TEXT NOT NULL, "
        "person TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cooked")
    op.execute("DROP TABLE IF EXISTS themedays")
    op.execute("ALTER TABLE meals DROP COLUMN leftovers")
    op.execute("ALTER TABLE meals DROP COLUMN chosen")
