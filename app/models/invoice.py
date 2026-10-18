import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import String, Float, Integer, Text, DateTime, Uuid, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )
    vendor_name: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
    )
    vendor_address: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )
    buyer_name: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
    )
    buyer_address: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )
    invoice_number: Mapped[str] = mapped_column(
        String(100),
        nullable=True,
    )
    invoice_date: Mapped[str] = mapped_column(
        String(50),
        nullable=True,
    )
    items: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )
    subtotal: Mapped[float] = mapped_column(
        Float,
        nullable=True,
    )
    tax: Mapped[float] = mapped_column(
        Float,
        nullable=True,
    )
    discount: Mapped[float] = mapped_column(
        Float,
        nullable=True,
    )
    total_amount: Mapped[float] = mapped_column(
        Float,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=True,
    )

    user: Mapped["User"] = relationship("User", back_populates="invoices")

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} user_id={self.user_id} invoice_number={self.invoice_number} total={self.total_amount}>"
