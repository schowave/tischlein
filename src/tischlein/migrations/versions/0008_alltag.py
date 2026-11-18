"""Alltag: Wunschliste, Vorschläge für Thema und Kochzeit, geschätzte Kochzeiten; „Reste“ als Grund
eines freien Tages heißt „Reste vom Vortag“ (der Reste-Tag eines Gerichts ist etwas anderes)."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # recipes the family wants in the next draft
    op.execute("CREATE TABLE IF NOT EXISTS wishes (recipe_uid TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT '')")
    # what the LLM proposes per recipe: kind 'thema' or 'zeit'; state 'offen' or 'abgelehnt' (not asked again)
    op.execute(
        "CREATE TABLE IF NOT EXISTS proposals (recipe_uid TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, "
        "state TEXT NOT NULL DEFAULT 'offen', PRIMARY KEY (recipe_uid, kind))"
    )
    # confirmed cooking times in minutes for recipes whose Paprika entry has none (stay in Tischlein)
    op.execute("CREATE TABLE IF NOT EXISTS cooktimes (recipe_uid TEXT PRIMARY KEY, minutes INTEGER NOT NULL)")
    op.execute("UPDATE skipdays SET reason = 'Reste vom Vortag' WHERE reason = 'Reste'")
    op.execute("UPDATE meals SET reason = 'Reste vom Vortag' WHERE reason = 'Reste' AND skipped = 1")


def downgrade() -> None:
    op.execute("UPDATE meals SET reason = 'Reste' WHERE reason = 'Reste vom Vortag' AND skipped = 1")
    op.execute("UPDATE skipdays SET reason = 'Reste' WHERE reason = 'Reste vom Vortag'")
    op.execute("DROP TABLE IF EXISTS cooktimes")
    op.execute("DROP TABLE IF EXISTS proposals")
    op.execute("DROP TABLE IF EXISTS wishes")
