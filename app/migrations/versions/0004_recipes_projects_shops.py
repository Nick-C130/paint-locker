"""painting recipes, projects, shops, bottle level and checks

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shops",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("url", sa.String(300), nullable=False, server_default=""),
        sa.Column("search_url", sa.String(300), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "shop_brands",
        sa.Column("shop_id", sa.Integer(), sa.ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("brand_id", sa.Integer(), sa.ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "recipes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "recipe_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipe_id", sa.Integer(), sa.ForeignKey("recipes.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("technique", sa.String(40), nullable=False, server_default="layer"),
        sa.Column("area", sa.String(120), nullable=False, server_default=""),
        sa.Column("paint_id", sa.Integer(), sa.ForeignKey("paints.id", ondelete="SET NULL")),
        sa.Column("mix_id", sa.Integer(), sa.ForeignKey("mix_recipes.id", ondelete="SET NULL")),
        sa.Column("dilution", sa.String(60), nullable=False, server_default=""),
        sa.Column("instructions", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="unit"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="SET NULL"), index=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("models_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("models_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "project_recipes",
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("recipe_id", sa.Integer(), sa.ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "project_paints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("paint_id", sa.Integer(), sa.ForeignKey("paints.id", ondelete="CASCADE")),
        sa.Column("mix_id", sa.Integer(), sa.ForeignKey("mix_recipes.id", ondelete="CASCADE")),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
    )
    with op.batch_alter_table("inventory_items") as b:
        b.add_column(sa.Column("level", sa.String(10), nullable=True))
        b.add_column(sa.Column("checked_on", sa.Date(), nullable=True))
    with op.batch_alter_table("shopping_items") as b:
        b.add_column(sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("shop_id", sa.Integer(), nullable=True))
        b.create_foreign_key("fk_shopping_items_shop_id", "shops", ["shop_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    with op.batch_alter_table("shopping_items") as b:
        b.drop_constraint("fk_shopping_items_shop_id", type_="foreignkey")
        b.drop_column("shop_id")
        b.drop_column("quantity")
    with op.batch_alter_table("inventory_items") as b:
        b.drop_column("checked_on")
        b.drop_column("level")
    for t in ("project_paints", "project_recipes", "projects", "recipe_steps", "recipes", "shop_brands", "shops"):
        op.drop_table(t)
