from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


PAINT_TYPES = [
    "acrylic", "air", "contrast", "wash", "ink", "metallic", "primer", "varnish",
    "medium", "pigment", "lacquer", "enamel", "oil",
]
FINISHES = ["matt", "satin", "gloss", "metallic"]
# Types that don't behave like ordinary opaque acrylics in a mix.
NON_MIXING_TYPES = {"wash", "contrast", "metallic", "primer", "varnish", "medium", "pigment"}
# Thinners, flow improvers, mediums and varnishes: they dilute or change finish, not colour.
ADDITIVE_TYPES = {"medium", "varnish"}
# Built-in mix components that aren't catalogue paints.
BUILTIN_ADDITIVES = {"water": "Water"}
MIX_UNITS = ["parts", "drops", "ml"]

# How bottles are grouped for the border style on rack views (see .cat-* in app.css).
TYPE_CATEGORY = {
    "acrylic": "acrylic",
    "air": "air",
    "medium": "thinner",
    "varnish": "thinner",
    "wash": "wash",
    "ink": "wash",
    "contrast": "wash",
    "metallic": "metallic",
    "primer": "primer",
}
CATEGORY_LABELS = [
    ("acrylic", "Brush acrylic"),
    ("air", "Airbrush-ready"),
    ("thinner", "Thinner / medium / varnish"),
    ("wash", "Wash / ink / contrast"),
    ("metallic", "Metallic"),
    ("primer", "Primer"),
    ("mix", "Your mix"),
    ("other", "Other (pigment, lacquer, enamel, oil)"),
]


def type_category(paint_type: str | None) -> str:
    return TYPE_CATEGORY.get(paint_type or "", "other")


ENTITY_TYPES = ["brand", "line", "paint", "item", "mix", "unit", "cell", "reference", "recipe", "project"]
TECHNIQUES = [
    "prime", "basecoat", "airbrush", "layer", "wash", "shade", "glaze", "drybrush", "highlight",
    "edge highlight", "filter", "weathering", "chipping", "pigment", "decal", "varnish", "other",
]
PROJECT_KINDS = ["army", "unit", "model", "diorama", "other"]
PROJECT_STATUSES = ["planned", "active", "done", "shelved"]
BOTTLE_LEVELS = [("full", "Full"), ("half", "About half"), ("low", "Running low")]


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class BottleFormat(Base):
    __tablename__ = "bottle_formats"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    volume_ml: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str] = mapped_column(Text, default="")


class Brand(Base):
    __tablename__ = "brands"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    lines: Mapped[list[ProductLine]] = relationship(back_populates="brand", order_by="ProductLine.name")


class ProductLine(Base):
    __tablename__ = "product_lines"
    __table_args__ = (UniqueConstraint("brand_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"))
    name: Mapped[str] = mapped_column(String(120))
    default_type: Mapped[str] = mapped_column(String(20), default="acrylic")
    default_finish: Mapped[str] = mapped_column(String(20), default="matt")
    default_volume_ml: Mapped[float | None] = mapped_column(Float)
    default_format_id: Mapped[int | None] = mapped_column(ForeignKey("bottle_formats.id"))
    discontinued: Mapped[bool] = mapped_column(Boolean, default=False)
    stocked_by: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(200), default="")

    brand: Mapped[Brand] = relationship(back_populates="lines")
    default_format: Mapped[BottleFormat | None] = relationship()
    paints: Mapped[list[Paint]] = relationship(back_populates="line")

    @property
    def label(self) -> str:
        return f"{self.brand.name} {self.name}"


class Paint(Base):
    __tablename__ = "paints"
    __table_args__ = (UniqueConstraint("line_id", "key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("product_lines.id"), index=True)
    key: Mapped[str] = mapped_column(String(160))  # code, or lower-cased name if no code
    code: Mapped[str] = mapped_column(String(40), default="", index=True)
    name: Mapped[str] = mapped_column(String(160))
    hex: Mapped[str | None] = mapped_column(String(7))
    measured_hex: Mapped[str | None] = mapped_column(String(7))
    type: Mapped[str] = mapped_column(String(20), default="acrylic")
    finish: Mapped[str] = mapped_column(String(20), default="matt")
    volume_ml: Mapped[float | None] = mapped_column(Float)
    barcode: Mapped[str] = mapped_column(String(40), default="", index=True)
    format_id: Mapped[int | None] = mapped_column(ForeignKey("bottle_formats.id"))
    discontinued: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="seed")  # seed | user
    # OKLab cached for fast colour-distance pre-filtering
    ok_l: Mapped[float | None] = mapped_column(Float)
    ok_a: Mapped[float | None] = mapped_column(Float)
    ok_b: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    line: Mapped[ProductLine] = relationship(back_populates="paints")
    format: Mapped[BottleFormat | None] = relationship()
    items: Mapped[list[InventoryItem]] = relationship(back_populates="paint")

    @property
    def display_hex(self) -> str | None:
        return self.measured_hex or self.hex

    @property
    def effective_format(self) -> BottleFormat | None:
        return self.format or self.line.default_format

    @property
    def label(self) -> str:
        code = f"{self.code} " if self.code else ""
        return f"{code}{self.name}"

    @property
    def full_label(self) -> str:
        return f"{self.line.brand.name} {self.line.name}: {self.label}"


class ReferenceColour(Base):
    __tablename__ = "reference_colours"
    __table_args__ = (UniqueConstraint("system", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(160))
    aliases: Mapped[str] = mapped_column(Text, default="")
    nation: Mapped[str] = mapped_column(String(40), default="")
    hex: Mapped[str | None] = mapped_column(String(7))
    source: Mapped[str] = mapped_column(Text, default="")

    @property
    def label(self) -> str:
        return f"{self.system} {self.code} {self.name}"


class ReferenceLink(Base):
    """Links a paint or mix to a historical reference colour."""

    __tablename__ = "reference_links"
    __table_args__ = (UniqueConstraint("reference_id", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    reference_id: Mapped[int] = mapped_column(ForeignKey("reference_colours.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(20))  # paint | mix
    entity_id: Mapped[int] = mapped_column(Integer)
    reference: Mapped[ReferenceColour] = relationship()


unit_formats = Table(
    "storage_unit_formats",
    Base.metadata,
    Column("unit_id", ForeignKey("storage_units.id", ondelete="CASCADE"), primary_key=True),
    Column("format_id", ForeignKey("bottle_formats.id", ondelete="CASCADE"), primary_key=True),
)


class StorageUnit(Base):
    __tablename__ = "storage_units"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    rows: Mapped[int] = mapped_column(Integer)
    cols: Mapped[int] = mapped_column(Integer)
    label_scheme: Mapped[str] = mapped_column(String(10), default="A1")  # A1 | 1-1
    location: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    formats: Mapped[list[BottleFormat]] = relationship(secondary=unit_formats, order_by="BottleFormat.name")
    cells: Mapped[list[StorageCell]] = relationship(
        back_populates="unit", cascade="all, delete-orphan", order_by="(StorageCell.row, StorageCell.col)"
    )


class StorageCell(Base):
    __tablename__ = "storage_cells"
    __table_args__ = (UniqueConstraint("unit_id", "row", "col"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("storage_units.id", ondelete="CASCADE"), index=True)
    row: Mapped[int] = mapped_column(Integer)  # 0-based
    col: Mapped[int] = mapped_column(Integer)  # 0-based
    label: Mapped[str] = mapped_column(String(20))
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)

    unit: Mapped[StorageUnit] = relationship(back_populates="cells")
    items: Mapped[list[InventoryItem]] = relationship(back_populates="cell")

    @property
    def full_label(self) -> str:
        return f"{self.unit.name}, {self.label}"


class MixRecipe(Base):
    __tablename__ = "mix_recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    intended_use: Mapped[str] = mapped_column(Text, default="")
    finish: Mapped[str] = mapped_column(String(20), default="matt")
    unit: Mapped[str] = mapped_column(String(10), default="parts")  # parts | drops | ml
    estimated_hex: Mapped[str | None] = mapped_column(String(7))
    measured_hex: Mapped[str | None] = mapped_column(String(7))
    target_hex: Mapped[str | None] = mapped_column(String(7))
    tags: Mapped[str] = mapped_column(Text, default="")
    format_id: Mapped[int | None] = mapped_column(ForeignKey("bottle_formats.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    format: Mapped[BottleFormat | None] = relationship()
    components: Mapped[list[MixComponent]] = relationship(
        back_populates="mix",
        cascade="all, delete-orphan",
        order_by="MixComponent.position",
        foreign_keys="MixComponent.mix_id",
    )
    items: Mapped[list[InventoryItem]] = relationship(back_populates="mix")

    @property
    def display_hex(self) -> str | None:
        return self.measured_hex or self.estimated_hex

    @property
    def label(self) -> str:
        return self.name

    @property
    def dilution(self) -> float:
        """Share of the recipe that is water/thinner/medium (0-1)."""
        total = sum(c.amount for c in self.components)
        return sum(c.amount for c in self.components if c.is_additive) / total if total else 0.0

    @property
    def ratio(self) -> str:
        """Amounts as a ratio, e.g. "2 : 1 : 1"."""
        return " : ".join(f"{c.amount:g}" for c in self.components)


class MixComponent(Base):
    __tablename__ = "mix_components"
    id: Mapped[int] = mapped_column(primary_key=True)
    mix_id: Mapped[int] = mapped_column(ForeignKey("mix_recipes.id", ondelete="CASCADE"), index=True)
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id"))
    sub_mix_id: Mapped[int | None] = mapped_column(ForeignKey("mix_recipes.id"))
    additive: Mapped[str | None] = mapped_column(String(20))  # built-in additive, e.g. "water"
    amount: Mapped[float] = mapped_column(Float, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)

    mix: Mapped[MixRecipe] = relationship(back_populates="components", foreign_keys=[mix_id])
    paint: Mapped[Paint | None] = relationship()
    sub_mix: Mapped[MixRecipe | None] = relationship(foreign_keys=[sub_mix_id])

    @property
    def is_additive(self) -> bool:
        """Water, thinner, flow improver, medium or varnish: dilutes rather than tints."""
        if self.additive:
            return True
        return bool(self.paint and self.paint.type in ADDITIVE_TYPES)

    @property
    def hex(self) -> str | None:
        if self.paint:
            return self.paint.display_hex
        if self.sub_mix:
            return self.sub_mix.display_hex
        return None

    @property
    def label(self) -> str:
        if self.paint:
            return self.paint.full_label
        if self.sub_mix:
            return f"Mix: {self.sub_mix.name}"
        if self.additive:
            return BUILTIN_ADDITIVES.get(self.additive, self.additive.title())
        return "?"


class InventoryItem(Base):
    """One physical bottle: a bought paint or a batch of a mix."""

    __tablename__ = "inventory_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), default="paint")  # paint | mix
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id"), index=True)
    mix_id: Mapped[int | None] = mapped_column(ForeignKey("mix_recipes.id"), index=True)
    cell_id: Mapped[int | None] = mapped_column(ForeignKey("storage_cells.id", ondelete="SET NULL"), index=True)
    format_id: Mapped[int | None] = mapped_column(ForeignKey("bottle_formats.id"))
    volume_ml: Mapped[float | None] = mapped_column(Float)
    acquired_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str | None] = mapped_column(String(10))  # full | half | low (None = not recorded)
    checked_on: Mapped[date | None] = mapped_column(Date)  # last "still good" check, resets the age alert
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    paint: Mapped[Paint | None] = relationship(back_populates="items")
    mix: Mapped[MixRecipe | None] = relationship(back_populates="items")
    cell: Mapped[StorageCell | None] = relationship(back_populates="items")
    format: Mapped[BottleFormat | None] = relationship()

    @property
    def hex(self) -> str | None:
        if self.paint:
            return self.paint.display_hex
        if self.mix:
            return self.mix.display_hex
        return None

    @property
    def label(self) -> str:
        if self.paint:
            return self.paint.label
        if self.mix:
            return self.mix.name
        return "?"

    @property
    def full_label(self) -> str:
        if self.paint:
            return self.paint.full_label
        if self.mix:
            return f"Mix: {self.mix.name}"
        return "?"

    @property
    def short_code(self) -> str:
        if self.paint:
            return self.paint.code or self.paint.name[:10]
        if self.mix:
            return self.mix.name[:10]
        return "?"

    @property
    def category(self) -> str:
        if self.mix_id:
            return "mix"
        return type_category(self.paint.type if self.paint else None)

    @property
    def effective_format(self) -> BottleFormat | None:
        if self.format:
            return self.format
        if self.paint:
            return self.paint.effective_format
        if self.mix:
            return self.mix.format
        return None


class ColourRequest(Base):
    """Log of every time a colour was looked for; feeds the phase-2 purchase recommender."""

    __tablename__ = "colour_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_hex: Mapped[str] = mapped_column(String(7))
    source: Mapped[str] = mapped_column(String(30))  # colour_search | mix_target | closest_owned | reference
    context: Mapped[str] = mapped_column(Text, default="")
    best_kind: Mapped[str | None] = mapped_column(String(10))  # paint | mix
    best_id: Mapped[int | None] = mapped_column(Integer)
    best_label: Mapped[str | None] = mapped_column(String(200))
    best_delta_e: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    body: Mapped[str] = mapped_column(Text)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(20), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(200))
    original_name: Mapped[str] = mapped_column(String(200), default="")
    content_type: Mapped[str] = mapped_column(String(100), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ShoppingItem(Base):
    __tablename__ = "shopping_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id"))
    text: Mapped[str] = mapped_column(Text, default="")
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    shop_id: Mapped[int | None] = mapped_column(ForeignKey("shops.id", ondelete="SET NULL"))  # None = automatic
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paint: Mapped[Paint | None] = relationship()
    shop: Mapped[Shop | None] = relationship()


shop_brands = Table(
    "shop_brands",
    Base.metadata,
    Column("shop_id", ForeignKey("shops.id", ondelete="CASCADE"), primary_key=True),
    Column("brand_id", ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True),
)


class Shop(Base):
    """A shop you buy from. It stocks a paint if you've ticked the brand, or the catalogue line's
    stocked_by names the shop."""

    __tablename__ = "shops"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    url: Mapped[str] = mapped_column(String(300), default="")
    search_url: Mapped[str] = mapped_column(String(300), default="")  # e.g. https://shop/search?q={q}
    notes: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    brands: Mapped[list[Brand]] = relationship(secondary=shop_brands, order_by="Brand.name")

    def stocks(self, paint: Paint) -> bool:
        if any(b.id == paint.line.brand_id for b in self.brands):
            return True
        return self.name.lower() in (paint.line.stocked_by or "").lower()

    def search_link(self, paint: Paint) -> str:
        if not self.search_url:
            return self.url
        from urllib.parse import quote_plus

        q = f"{paint.line.brand.name} {paint.code or ''} {paint.name}".replace("  ", " ").strip()
        return self.search_url.replace("{q}", quote_plus(q))


class Recipe(Base):
    """A painting guide: ordered steps, each optionally using a paint or a mix."""

    __tablename__ = "recipes"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    steps: Mapped[list[RecipeStep]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan", order_by="RecipeStep.position"
    )

    @property
    def label(self) -> str:
        return self.name

    @property
    def paint_ids(self) -> list[int]:
        return list(dict.fromkeys(s.paint_id for s in self.steps if s.paint_id))

    @property
    def mix_ids(self) -> list[int]:
        return list(dict.fromkeys(s.mix_id for s in self.steps if s.mix_id))


class RecipeStep(Base):
    __tablename__ = "recipe_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    technique: Mapped[str] = mapped_column(String(40), default="layer")
    area: Mapped[str] = mapped_column(String(120), default="")  # e.g. "tunic", "tracks", "whole model"
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id", ondelete="SET NULL"))
    mix_id: Mapped[int | None] = mapped_column(ForeignKey("mix_recipes.id", ondelete="SET NULL"))
    dilution: Mapped[str] = mapped_column(String(60), default="")  # e.g. "2:1 with water"
    instructions: Mapped[str] = mapped_column(Text, default="")

    recipe: Mapped[Recipe] = relationship(back_populates="steps")
    paint: Mapped[Paint | None] = relationship()
    mix: Mapped[MixRecipe | None] = relationship()

    @property
    def hex(self) -> str | None:
        if self.paint:
            return self.paint.display_hex
        if self.mix:
            return self.mix.display_hex
        return None


project_recipes = Table(
    "project_recipes",
    Base.metadata,
    Column("project_id", ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True),
    Column("recipe_id", ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
)


class Project(Base):
    """An army, unit, model or diorama. Armies can contain units (parent_id)."""

    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20), default="unit")
    status: Mapped[str] = mapped_column(String(20), default="active")
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    models_total: Mapped[int] = mapped_column(Integer, default=0)
    models_done: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    parent: Mapped[Project | None] = relationship(remote_side="Project.id", back_populates="children")
    children: Mapped[list[Project]] = relationship(back_populates="parent", order_by="Project.name")
    recipes: Mapped[list[Recipe]] = relationship(secondary=project_recipes, order_by="Recipe.name")
    extras: Mapped[list[ProjectPaint]] = relationship(back_populates="project", cascade="all, delete-orphan")

    @property
    def label(self) -> str:
        return self.name

    def all_projects(self) -> list[Project]:
        """This project and every unit under it."""
        out, todo, seen = [], [self], set()
        while todo:
            p = todo.pop(0)
            if p.id in seen:
                continue
            seen.add(p.id)
            out.append(p)
            todo.extend(p.children)
        return out

    @property
    def progress(self) -> tuple[int, int]:
        ps = self.all_projects()
        return sum(p.models_done for p in ps), sum(p.models_total for p in ps)


class ProjectPaint(Base):
    """A paint or mix a project uses directly (outside any recipe)."""

    __tablename__ = "project_paints"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id", ondelete="CASCADE"))
    mix_id: Mapped[int | None] = mapped_column(ForeignKey("mix_recipes.id", ondelete="CASCADE"))
    note: Mapped[str] = mapped_column(String(200), default="")

    project: Mapped[Project] = relationship(back_populates="extras")
    paint: Mapped[Paint | None] = relationship()
    mix: Mapped[MixRecipe | None] = relationship()


class BarcodeLink(Base):
    """A scanned barcode (EAN/UPC) you've linked to a catalogue paint. Kept separate from the
    seeded catalogue so re-importing the CSVs never wipes your links."""

    __tablename__ = "barcode_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    barcode: Mapped[str] = mapped_column(String(40), unique=True)
    paint_id: Mapped[int] = mapped_column(ForeignKey("paints.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paint: Mapped[Paint] = relationship()
