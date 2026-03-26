import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


class UserRole(str, enum.Enum):
    agent = "agent"
    client = "client"


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="tenant")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id", ondelete="SET NULL"), index=True, nullable=True)
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
    clients: Mapped[list["Client"]] = relationship(
        back_populates="agent",
        cascade="all, delete-orphan",
        foreign_keys="Client.agent_user_id",
    )
    commissions: Mapped[list["AgentCommission"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    tenant: Mapped["Tenant | None"] = relationship(back_populates="users")
    credential: Mapped["AgentCredential | None"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")


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
    notes: Mapped[list["ApplicationNote"]] = relationship(back_populates="application", cascade="all, delete-orphan")


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
    note_id: Mapped[int | None] = mapped_column(ForeignKey("application_notes.id", ondelete="SET NULL"), index=True, nullable=True)

    agent: Mapped["User"] = relationship(back_populates="reminders")
    note: Mapped["ApplicationNote | None"] = relationship(back_populates="reminders")


class ApplicationNote(Base):
    __tablename__ = "application_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True, nullable=False)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    application: Mapped["Application"] = relationship(back_populates="notes")
    agent: Mapped["User"] = relationship()
    reminders: Mapped[list["Reminder"]] = relationship(back_populates="note")


class AgentCommission(Base):
    __tablename__ = "agent_commissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_kind: Mapped[str] = mapped_column(String(200), nullable=False)
    percent_bp: Mapped[int] = mapped_column(Integer, nullable=False)  # basis points: 12.5% -> 1250
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    agent: Mapped["User"] = relationship(back_populates="commissions")


class AgentCredential(Base):
    __tablename__ = "agent_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    salt: Mapped[str] = mapped_column(String(255), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="credential")


class InviteStatus(str, enum.Enum):
    active = "active"
    used = "used"
    revoked = "revoked"
    expired = "expired"


class AgentInvite(Base):
    __tablename__ = "agent_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    target_client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True, nullable=True)
    token: Mapped[str] = mapped_column(String(96), unique=True, index=True, nullable=False)
    is_public: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, name="invite_status"),
        default=InviteStatus.active,
        nullable=False,
    )
    uses_left: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["Tenant"] = relationship()
    agent: Mapped["User"] = relationship(foreign_keys=[agent_user_id])
    used_by_user: Mapped["User | None"] = relationship(foreign_keys=[used_by_user_id])


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agent_user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    source_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    agent: Mapped["User"] = relationship(back_populates="clients", foreign_keys=[agent_user_id])
    source_user: Mapped["User | None"] = relationship(foreign_keys=[source_user_id])
    contracts: Mapped[list["Contract"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    documents: Mapped[list["ClientDocument"]] = relationship(back_populates="client", cascade="all, delete-orphan")

class ContractStatus(str, enum.Enum):
    active = "active"
    terminated = "terminated"


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

    # Действует/прекращен — важно для дальнейшей логики оплаты/комиссий.
    status: Mapped["ContractStatus"] = mapped_column(
        Enum(ContractStatus, name="contract_status"),
        default=ContractStatus.active,
        nullable=False,
        index=True,
    )

    # Annual insurance premium (used for payment schedule).
    total_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    # Insured sum / coverage amount.
    insured_sum_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
