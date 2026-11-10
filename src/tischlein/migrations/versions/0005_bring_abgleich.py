"""Bring-Abgleich: welche Einträge der Einkaufsliste schon in Bring stehen."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("shopping")}
    # bring: 0 = not on the Bring list, 1 = open there (with bring_menge as sent), 2 = ticked off there.
    # Only changes are sent, so Bring does not notify everyone about the whole list again.
    if "bring" not in columns:
        op.execute("ALTER TABLE shopping ADD COLUMN bring INTEGER NOT NULL DEFAULT 0")
    if "bring_menge" not in columns:
        op.execute("ALTER TABLE shopping ADD COLUMN bring_menge TEXT NOT NULL DEFAULT ''")


def downgrade() -> None:
    with op.batch_alter_table("shopping") as batch:
        batch.drop_column("bring_menge")
        batch.drop_column("bring")
