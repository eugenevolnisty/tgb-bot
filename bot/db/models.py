import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


class UserRole(str, enum.Enum):
    agent = "agent"
    client = "client"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    role: Mapped[UserRole | None] = mapped_column(Enum(UserRole, name="user_role"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    applications: Mapped[list["Application"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    quotes: Mapped[list["Quote"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class ApplicationStatus(str, enum.Enum):
    new = "new"
    in_progress = "in_progress"
    done = "done"


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus, name="application_status"),
        default=ApplicationStatus.new,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), default="Заявка", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    client: Mapped["User"] = relationship(back_populates="applications")
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id", ondelete="SET NULL"), index=True, nullable=True)
    quote: Mapped["Quote"] = relationship(back_populates="application")


class QuoteType(str, enum.Enum):
    kasko = "kasko"
    property = "property"
    cargo = "cargo"
    accident = "accident"
    cmr = "cmr"
    dms = "dms"
    other = "other"


class Quote(Base):
    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    quote_type: Mapped[QuoteType] = mapped_column(Enum(QuoteType, name="quote_type"), nullable=False)

    input_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-строка входных параметров
    premium_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # хранение в копейках/центах
    currency: Mapped[str] = mapped_column(String(8), default="BYN", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    client: Mapped["User"] = relationship(back_populates="quotes")
    application: Mapped["Application"] = relationship(back_populates="quote", uselist=False)


class ReminderStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    cancelled = "cancelled"


class ReminderRepeat(str, enum.Enum):
    none = "none"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[ReminderStatus] = mapped_column(
        Enum(ReminderStatus, name="reminder_status"),
        default=ReminderStatus.pending,
        nullable=False,
    )
    repeat: Mapped[ReminderRepeat] = mapped_column(
        Enum(ReminderRepeat, name="reminder_repeat"),
        default=ReminderRepeat.none,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["User"] = relationship(back_populates="reminders")
