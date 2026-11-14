"""Modelos persistentes de AppBoletas."""
from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, false, func, select, true
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.db.base import Base


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("rut", name="uq_clients_rut"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rut: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    razon_social: Mapped[str] = mapped_column(String(100), nullable=False)
    nombre_fantasia: Mapped[str | None] = mapped_column(String(100), nullable=True)
    giro: Mapped[str | None] = mapped_column(String(80), nullable=True)
    direccion: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[int] = mapped_column(nullable=False)
    comuna: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    detalle_default: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BHEEmission(Base):
    __tablename__ = "bhe_emissions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'success', 'failed', 'awaiting_final_confirmation', 'finalizing', 'emission_status_unknown')",
            name="ck_bhe_emissions_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client: Mapped[Client | None] = relationship(
        "Client", foreign_keys=[client_id], viewonly=True, lazy="joined"
    )
    batch_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("bhe_batch_items.id", ondelete="SET NULL", use_alter=True), nullable=True, index=True
    )
    rut_receptor: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    razon_social: Mapped[str] = mapped_column(String(100), nullable=False)
    direccion: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[int] = mapped_column(nullable=False)
    comuna: Mapped[str] = mapped_column(String(80), nullable=False)
    detalle: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    monto_total: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha_emision_solicitada: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Kept as historical metadata; new emissions always persist "sii".
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="sii", index=True)
    provider_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    folio: Mapped[int | None] = mapped_column(Integer, nullable=True)
    codigo_barras: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fecha_emision_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pdf_base64: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sii_resolved_recipient_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    retention_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Sanitized SII retention selection/preview diagnostics; never stores page text.
    sii_retention_diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def nombre_fantasia(self) -> str | None:
        """Current visual client name; fiscal snapshots remain on this row."""
        return self.client.nombre_fantasia if self.client is not None else None


class BHEBatch(Base):
    __tablename__ = "bhe_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'ready', 'processing', 'completed', 'completed_with_errors')",
            name="ck_bhe_batches_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    fecha_emision: Mapped[date] = mapped_column(Date, nullable=False)
    detalle_global: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Nullable for historical batches whose retention mode was never recorded.
    retention_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Kept so historical SimpleFactura batches remain readable.
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="sii", server_default="simplefactura", index=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="monthly", server_default="monthly", index=True)
    source_batch_id: Mapped[int | None] = mapped_column(ForeignKey("bhe_batches.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["BHEBatchItem"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="BHEBatchItem.id"
    )


class BHEBatchItem(Base):
    __tablename__ = "bhe_batch_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "client_id", name="uq_bhe_batch_items_batch_client"),
        CheckConstraint(
            "status IN ('draft', 'pending', 'success', 'failed', 'emission_status_unknown')",
            name="ck_bhe_batch_items_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("bhe_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_id: Mapped[int | None] = mapped_column(
        ForeignKey("clients.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    rut_receptor: Mapped[str] = mapped_column(String(20), nullable=False)
    razon_social: Mapped[str] = mapped_column(String(100), nullable=False)
    direccion: Mapped[str] = mapped_column(String(80), nullable=False)
    region: Mapped[int] = mapped_column(nullable=False)
    comuna: Mapped[str] = mapped_column(String(80), nullable=False)
    giro: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    fecha_emision: Mapped[date | None] = mapped_column(Date, nullable=True)
    detalle: Mapped[list[dict]] = mapped_column(JSON, nullable=False)
    # Null means that the item inherits BHEBatch.retention_mode.
    retention_mode_override: Mapped[str | None] = mapped_column(String(32), nullable=True)
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Points to the latest attempt; all attempts are discoverable through BHEEmission.batch_item_id.
    bhe_emission_id: Mapped[int | None] = mapped_column(
        ForeignKey("bhe_emissions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_click_occurred: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    folio: Mapped[int | None] = column_property(
        select(BHEEmission.folio)
        .where(BHEEmission.id == bhe_emission_id)
        .correlate_except(BHEEmission)
        .scalar_subquery()
    )

    batch: Mapped[BHEBatch] = relationship(back_populates="items")
