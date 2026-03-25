import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
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
    clients: Mapped[list["Client"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


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
    expeditor = "expeditor"
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


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped["User"] = relationship(back_populates="clients")
    contracts: Mapped[list["Contract"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    documents: Mapped[list["ClientDocument"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False)

    contract_number: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_kind: Mapped[str] = mapped_column(String(200), nullable=False)
    vehicle_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)

    total_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)  # BYN * 100
    currency: Mapped[str] = mapped_column(String(8), default="BYN", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    client: Mapped["Client"] = relationship(back_populates="contracts")
    payments: Mapped[list["Payment"]] = relationship(back_populates="contract", cascade="all, delete-orphan")
    documents: Mapped[list["ContractDocument"]] = relationship(back_populates="contract", cascade="all, delete-orphan")


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True, nullable=False)

    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)  # BYN * 100
    due_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)

    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"),
        default=PaymentStatus.pending,
        nullable=False,
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contract: Mapped["Contract"] = relationship(back_populates="payments")


class ClientDocument(Base):
    __tablename__ = "client_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False)

    # Telegram file_id is stable; store it to re-send photo later.
    file_id: Mapped[str] = mapped_column(String(250), nullable=False)
    file_unique_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    client: Mapped["Client"] = relationship(back_populates="documents")


class ContractDocument(Base):
    __tablename__ = "contract_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("contracts.id", ondelete="CASCADE"), index=True, nullable=False)

    file_id: Mapped[str] = mapped_column(String(250), nullable=False)
    file_unique_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    contract: Mapped["Contract"] = relationship(back_populates="documents")
