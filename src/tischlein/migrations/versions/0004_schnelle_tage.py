"""Schnelle Tage: an diesen Tagen plant Tischlein zuerst Gerichte bis 30 Minuten."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE IF NOT EXISTS quickdays (day TEXT PRIMARY KEY)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS quickdays")
