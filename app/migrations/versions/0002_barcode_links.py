"""barcode links

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "barcode_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(length=40), nullable=False),
        sa.Column("paint_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.ForeignKeyConstraint(["paint_id"], ["paints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("barcode"),
    )
    with op.batch_alter_table("barcode_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_barcode_links_paint_id"), ["paint_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("barcode_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_barcode_links_paint_id"))
    op.drop_table("barcode_links")
