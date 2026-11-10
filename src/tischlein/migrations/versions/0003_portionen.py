"""Portionen pro geplantem Gericht: skalieren die Mengen in der Einkaufsliste."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # keyed by day and recipe; Store.save_plan drops rows whose dish is no longer planned that day,
    # so a swapped or re-rolled day starts again at the family portions (tischlein.toml portionen)
    op.execute("""CREATE TABLE IF NOT EXISTS servings (
        day TEXT NOT NULL,
        recipe_uid TEXT NOT NULL,
        servings INTEGER NOT NULL CHECK (servings > 0),
        PRIMARY KEY (day, recipe_uid))""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS servings")
