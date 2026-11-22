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
MIX_UNITS = ["parts", "drops", "ml"]
ENTITY_TYPES = ["brand", "line", "paint", "item", "mix", "unit", "cell", "reference"]


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


class MixComponent(Base):
    __tablename__ = "mix_components"
    id: Mapped[int] = mapped_column(primary_key=True)
    mix_id: Mapped[int] = mapped_column(ForeignKey("mix_recipes.id", ondelete="CASCADE"), index=True)
    paint_id: Mapped[int | None] = mapped_column(ForeignKey("paints.id"))
    sub_mix_id: Mapped[int | None] = mapped_column(ForeignKey("mix_recipes.id"))
    amount: Mapped[float] = mapped_column(Float, default=1)
    position: Mapped[int] = mapped_column(Integer, default=0)

    mix: Mapped[MixRecipe] = relationship(back_populates="components", foreign_keys=[mix_id])
    paint: Mapped[Paint | None] = relationship()
    sub_mix: Mapped[MixRecipe | None] = relationship(foreign_keys=[sub_mix_id])

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
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    paint: Mapped[Paint | None] = relationship()
