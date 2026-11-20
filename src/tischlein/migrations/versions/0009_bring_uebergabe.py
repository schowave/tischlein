"""Bring-Übergabe statt Abgleich in beide Richtungen: pro Woche, was Tischlein an Bring übergeben hat.

Der Stand hängt nicht mehr an den Zeilen der Einkaufsliste: Ein gelöschter oder beim Neu-Sammeln
weggefallener Eintrag muss beim nächsten Übergeben noch aus Bring entfernt werden können."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # name as sent (Bring finds its item by name), menge = amount as sent
    op.execute(
        "CREATE TABLE IF NOT EXISTS bring_sent (week_start TEXT NOT NULL, name TEXT NOT NULL, "
        "menge TEXT NOT NULL DEFAULT '', PRIMARY KEY (week_start, name))"
    )
    # what is open in Bring today (bring = 1) counts as handed over, so the first click adds nothing twice
    op.execute(
        "INSERT OR IGNORE INTO bring_sent (week_start, name, menge) "
        "SELECT week_start, name, bring_menge FROM shopping WHERE bring = 1"
    )
    op.execute("ALTER TABLE shopping DROP COLUMN bring")
    op.execute("ALTER TABLE shopping DROP COLUMN bring_menge")
    op.execute("DELETE FROM meta WHERE key IN ('bring_sync', 'bring_week') OR key LIKE 'bring\\_gone:%' ESCAPE '\\'")


def downgrade() -> None:
    op.execute("ALTER TABLE shopping ADD COLUMN bring INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE shopping ADD COLUMN bring_menge TEXT NOT NULL DEFAULT ''")
    op.execute(
        "UPDATE shopping SET bring = 1, bring_menge = (SELECT menge FROM bring_sent b "
        "WHERE b.week_start = shopping.week_start AND b.name = shopping.name) "
        "WHERE EXISTS (SELECT 1 FROM bring_sent b WHERE b.week_start = shopping.week_start AND b.name = shopping.name)"
    )
    op.execute("DELETE FROM meta WHERE key LIKE 'bring\\_stand:%' ESCAPE '\\'")
    op.execute("DROP TABLE bring_sent")
