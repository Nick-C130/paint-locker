"""mix component additives (water)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mix_components", schema=None) as batch_op:
        batch_op.add_column(sa.Column("additive", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("mix_components", schema=None) as batch_op:
        batch_op.drop_column("additive")
