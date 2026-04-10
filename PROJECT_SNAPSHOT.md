# PROJECT_SNAPSHOT.md
> Обновлён после: Багфикс orphan-расчётов при удалении компании/вида
> Следующий этап: Этап 1.4б — Проверка расчёта (калькулятор)

---

## ОПИСАНИЕ ПРОЕКТА
Telegram-бот для страховых агентов.
Стек: Python, aiogram 3, SQLAlchemy async, PostgreSQL, Render.

---

## СТРУКТУРА ФАЙЛОВ
bot/
init.py
main.py
config.py
keyboards.py
db/
init.py
base.py
models.py
repo.py
handlers/
init.py
router.py
start.py
agent.py
client.py
clients.py
superadmin.py
application_flow.py
reminders.py
kasko.py
property_calc.py
generic_calcs.py
payment_reports.py
contract_reports.py
commission_reports.py
middlewares/
access_guard.py
scheduler/
payment_reminders.py
reminders.py
services/
agent_auth.py
datetime_parse.py
generic_calc.py
kasko.py
property.py
accident_travel.py
expeditor.py
seed_due_payment_test_data.py
seed_random_clients.py

text


---

## КЛЮЧЕВЫЕ ПАТТЕРНЫ

```python
# Сессии
async with get_session_maker()() as session: ...

# Роль из middleware
is_superadmin = data["is_superadmin"]  # bool
agent_tg_id = message.from_user.id

# FSM пример
class ClientAdd(StatesGroup): ...
# Rolling clean через _flow_answer()

# Клавиатуры — InlineKeyboardMarkup напрямую
CONFIG (bot/config.py) — класс Settings
Поле	Тип	Дефолт
bot_token	str	—
database_url	str	—
log_level	str	"INFO"
timezone	str	"Europe/Minsk"
dev_role_switch_enabled	bool	True
superadmin_tg_id	int	0
МОДЕЛИ БД (bot/db/models.py)
Enums
Python

UserRole: agent | client | superadmin
ApplicationStatus: new | in_progress | done
QuoteType: kasko | property | cargo | accident | expeditor | cmr | dms | other
ReminderStatus: pending | sent | cancelled
ReminderRepeat: none | daily | weekly | monthly
InviteStatus: active | used | revoked | expired
ContractStatus: active | terminated
PaymentStatus: pending | paid
Таблицы
text

Tenant          id, code, title, created_at
User            id, tg_id, display_name, role, tenant_id,
                agent_contact_phones_json, agent_contact_email,
                agent_contact_telegram, created_at, updated_at
AgentCredential id, user_id, password_hash, salt,
                failed_attempts, locked_until
AgentInvite     id, tenant_id, agent_user_id, target_client_id,
                invite_type(client|agent_registration),
                token, is_public, status, uses_left,
                expires_at, used_at, used_by_user_id
AgentCommission id, agent_user_id, company, contract_kind,
                percent_bp (basis points: 12.5% → 1250)
Client          id, agent_user_id, source_user_id,
                full_name, phone, email
Contract        id, client_id, contract_number, company,
                contract_kind, vehicle_description,
                start_date, end_date, status,
                total_amount_minor, insured_sum_minor, currency
Payment         id, contract_id, amount_minor, due_date,
                status, paid_at
ClientDocument  id, client_id, file_id, file_unique_id, caption
ContractDocument id, contract_id, file_id, file_unique_id, caption
Application     id, client_user_id, status, title,
                description, quote_id
Quote           id, client_user_id, quote_type,
                input_json, premium_amount, currency
Reminder        id, agent_user_id, text, remind_at,
                status, repeat, sent_at, note_id
ApplicationNote id, application_id, agent_user_id, text
РОУТЕРЫ (bot/handlers/router.py)
Python

# Порядок важен — superadmin первым
router.include_router(superadmin_router)
router.include_router(start_router)
router.include_router(client_router)
router.include_router(agent_router)
router.include_router(kasko_router)
router.include_router(application_router)
router.include_router(property_router)
router.include_router(generic_router)
router.include_router(reminders_router)
router.include_router(clients_router)
router.include_router(payment_reports_router)
router.include_router(contract_reports_router)
router.include_router(commission_reports_router)

# Middleware на все роутеры
router.message.outer_middleware(AccessGuardMiddleware())
router.callback_query.outer_middleware(AccessGuardMiddleware())
REPO.PY — ФУНКЦИИ ПО ГРУППАМ
Пользователи / Тенанты
Python

get_or_create_user(tg_id) -> User
set_user_role(tg_id, role, *, display_name=None) -> User
set_superadmin(tg_id) -> User
get_all_agents() -> list[User]
get_agent_by_tg_id(tg_id) -> User | None
count_clients_by_agent(agent_user_id) -> int
get_all_tenants() -> list[Tenant]
get_user_display_name(tg_id) -> str | None
set_agent_display_name(agent_tg_id, display_name) -> bool
get_agent_contacts(agent_tg_id) -> tuple[list[str], str|None, str|None]
set_agent_contacts(agent_tg_id, phones, email, telegram) -> bool
has_agent_footprint(tg_id) -> bool
block_agent(agent_tg_id) -> bool
reset_test_clients() -> int
reset_test_agents(exclude_tg_id=None) -> int
Пароли агента
Python

set_agent_password(agent_tg_id, password) -> bool
verify_agent_password(agent_tg_id, password) -> bool
has_agent_password(agent_tg_id) -> bool
Инвайты
Python

create_agent_invite(agent_tg_id, *, ttl_hours, uses_left,
                    target_client_id) -> AgentInvite | None
create_superadmin_invite(sa_tg_id, ttl_hours) -> AgentInvite | None
create_client_bind_invite(agent_tg_id, client_id, *, ttl_hours) -> AgentInvite | None
get_agent_invite_by_token(token) -> AgentInvite | None
validate_private_invite_for_action(token) -> tuple[AgentInvite|None, str]
consume_agent_registration_invite(token, agent_tg_id) -> tuple[bool, str]
consume_agent_invite(token, client_tg_id, *, first_name,
                     last_name, username) -> tuple[bool, str]
revoke_agent_invite(agent_tg_id, invite_id) -> bool
get_or_create_public_agent_link(agent_tg_id) -> AgentInvite | None
regenerate_public_agent_link(agent_tg_id) -> AgentInvite | None
consume_public_agent_link(token, client_tg_id, *, first_name,
                          last_name, username) -> tuple[bool, str]
list_agent_invites(agent_tg_id, limit) -> list[AgentInvite]
list_invited_client_user_ids(agent_tg_id) -> set[int]
Клиенты
Python

create_client(agent_tg_id, full_name, phone, email,
              source_user_id=None) -> Client
list_clients(agent_tg_id, query=None, limit=20) -> list[Client]
list_clients_page(agent_tg_id, query=None, *, limit, offset,
                  invited_only) -> list[Client]
get_client(agent_tg_id, client_id) -> Client | None
update_client(agent_tg_id, client_id, full_name,
              phone, email) -> Client | None
delete_client(agent_tg_id, client_id) -> bool
Договоры
Python

create_contract_for_client(agent_tg_id, client_id, contract_number,
    company, contract_kind, start_date, end_date,
    total_amount_minor, insured_sum_minor, currency,
    initial_payment_amount_minor, initial_payment_due_date,
    payments, vehicle_description=None) -> Contract | None
list_contracts_for_client(agent_tg_id, client_id, limit) -> list[Contract]
get_contract_detailed(agent_tg_id, contract_id) -> Contract | None
search_contracts_by_number(agent_tg_id, query, limit) -> list[Contract]
update_contract_for_client(...) -> Contract | None
terminate_contract(agent_tg_id, contract_id) -> bool
delete_contract(agent_tg_id, contract_id) -> bool
Платежи
Python

mark_payment_paid(agent_tg_id, payment_id) -> int | None
report_client_payment_with_adjustment(client_tg_id, contract_id,
    paid_date, amount_minor) -> tuple[int, str, int] | None
Документы
Python

create_client_document(agent_tg_id, client_id, file_id,
    file_unique_id, caption) -> ClientDocument | None
list_client_documents(agent_tg_id, client_id, limit) -> list[ClientDocument]
get_client_document(agent_tg_id, doc_id) -> ClientDocument | None
delete_client_document(agent_tg_id, doc_id) -> bool
create_contract_document(agent_tg_id, contract_id, file_id,
    file_unique_id, caption) -> ContractDocument | None
list_contract_documents(agent_tg_id, contract_id, limit) -> list[ContractDocument]
get_contract_document(agent_tg_id, doc_id) -> ContractDocument | None
contract_has_documents(agent_tg_id, contract_id) -> bool
delete_contract_document(agent_tg_id, doc_id) -> bool
Комиссии
Python

list_agent_companies_for_commission(agent_tg_id, limit) -> list[str]
list_agent_contract_kinds_for_company(agent_tg_id, company, limit) -> list[str]
upsert_agent_commission(agent_tg_id, company, contract_kind,
    percent_bp) -> AgentCommission | None
list_agent_commissions(agent_tg_id, limit) -> list[AgentCommission]
list_agent_company_kind_pairs(agent_tg_id, limit) -> list[tuple[str, str]]
Напоминания
Python

create_reminder(agent_tg_id, text_value, remind_at_utc,
    repeat, note_id=None) -> Reminder
list_agent_reminders(agent_tg_id, limit) -> list[Reminder]
fetch_due_reminders(limit) -> list[tuple[Reminder, int]]
mark_reminder_sent(reminder_id) -> None
set_reminder_repeat(reminder_id, repeat) -> None
cancel_reminder(reminder_id) -> None
reschedule_recurring_reminder(reminder_id, next_remind_at_utc) -> None
delete_reminder(reminder_id) -> None
update_reminder_datetime(reminder_id, remind_at_utc) -> None
Заявки / Котировки
Python

create_application_for_client(tg_id, description=None) -> Application
list_incoming_applications(agent_tg_id, limit) -> list[Application]
list_in_progress_applications(agent_tg_id, limit) -> list[Application]
create_kasko_quote(tg_id, input_payload, premium_byn, currency) -> Quote
create_property_quote(tg_id, input_payload, premium_byn, currency) -> Quote
create_generic_quote(tg_id, quote_type, input_payload,
    premium_byn, currency) -> Quote
create_application_from_quote(tg_id, quote_id) -> Application
set_application_status(agent_tg_id, app_id, status) -> Application | None
delete_application(agent_tg_id, app_id) -> Application | None
create_application_note(agent_tg_id, app_id, text_value) -> ApplicationNote | None
list_notes_for_application(agent_tg_id, app_id, limit) -> list[ApplicationNote]
list_notes_for_agent(agent_tg_id, limit) -> list[ApplicationNote]
get_note_for_agent(agent_tg_id, note_id) -> ApplicationNote | None
delete_note_for_agent(agent_tg_id, note_id) -> bool
Клиент ↔ Агент (привязки)
Python

list_tenant_agent_tg_ids_for_client(client_tg_id) -> list[int]
get_bound_client_profile(client_tg_id) -> tuple[str|None,str|None,str|None]|None
list_bound_client_tg_for_agent(agent_tg_id) -> list[tuple[int, str]]
get_client_nearest_payment_or_contract_end(client_tg_id) -> tuple[str, dict]|None
get_bound_agent_and_client_for_user(client_tg_id) -> tuple[int, int] | None
get_bound_agent_contact_for_client(client_tg_id) -> tuple|None
update_bound_client_phone(client_tg_id, phone) -> bool
get_contract_bound_client_tg(agent_tg_id, contract_id) -> tuple[int,str]|None
get_contract_for_client_user(client_tg_id, contract_id) -> Contract|None
list_contracts_for_client_user(client_tg_id, limit) -> list[Contract]
list_contract_documents_for_client_user(client_tg_id, contract_id, limit)
get_contract_document_for_client_user(client_tg_id, doc_id)
create_contract_document_for_client_user(client_tg_id, contract_id,
    file_id, file_unique_id, caption)
list_client_documents_for_client_user(client_tg_id, limit)
get_client_document_for_client_user(client_tg_id, doc_id)
create_client_document_for_client_user(client_tg_id, file_id,
    file_unique_id, caption)
main.py — STARTUP
Python

on_startup():
    await init_db()
    await migrate_agent_tenants()
    if settings.superadmin_tg_id:
        await set_superadmin(settings.superadmin_tg_id)

main():
    await on_startup()
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.create_task(reminders_worker(bot))
    asyncio.create_task(payment_reminders_worker(bot))
    await dp.start_polling(bot)
СТАТУС ЭТАПОВ
Этап	Описание	Статус
0.1	Супер-админ основа	✅
0.2	Исправление авто-отчётов	✅
0.3	Онбординг агента по инвайту	✅
UX фикс меню настроек агента	✅
UX фикс broadcast + ForceReply	✅
1.1	Структура данных тарифов	⬜
1.2	Настройка при онбординге	⬜
1.3	Гибкие тарифные карты	⬜
1.4	Калькулятор на основе тарифов	⬜
1.4а	FSM создания тарифа КАСКО	✅
1.5	Универсальный калькулятор	✅
Фикс редактирования и копирования тарифа	✅
Редактирование при копировании	✅
Редактирование коэффициентов	✅
Частые коэффициенты КАСКО	✅
Фикс удаления компании/вида (очистка расчётов)	✅
2–6	...	⬜
```
