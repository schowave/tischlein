"""Einkauf: von Hand gelöschte Zutaten merken, damit „Neu sammeln“ sie nicht zurückholt."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # item_key = shopping.item_key(name), recipes = the dishes it was for ("A, B"). Per week, and only
    # while no other dish needs it: swap in a dish that needs it too and it is collected again.
    op.execute(
        "CREATE TABLE IF NOT EXISTS shopping_removed ("
        "week_start TEXT NOT NULL, item_key TEXT NOT NULL, recipes TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (week_start, item_key))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE shopping_removed")
