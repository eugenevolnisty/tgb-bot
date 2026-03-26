import argparse
import asyncio
import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select

from bot.db.base import get_session_maker, init_db
from bot.db.models import User, UserRole
from bot.db.repo import create_client, create_contract_for_client


_NAMES_MALE = ["Иван", "Александр", "Дмитрий", "Николай", "Максим", "Сергей", "Павел", "Артем"]
_NAMES_FEMALE = ["Анна", "Екатерина", "Ольга", "Мария", "Татьяна", "Наталья", "Виктория", "Ирина"]
_PATRONYMICS_MALE = ["Иванович", "Александрович", "Дмитриевич", "Николаевич", "Максимович", "Сергеевич", "Павлович"]
_PATRONYMICS_FEMALE = ["Ивановна", "Александровна", "Дмитриевна", "Николаевна", "Максимовна", "Сергеевна", "Павловна"]
_LASTNAMES = ["Иванов", "Петров", "Сидоров", "Кузнецов", "Попов", "Смирнов", "Федоров", "Соколов", "Морозов"]

_COMPANIES = ["ООО Альфа", "ООО Бета", "ООО Гамма", "ЧП Дельта", "ЗАО Эхо", "ИП Лидер", "АО Сфера"]
_CARS = [
    "Toyota Camry 2020",
    "Volkswagen Passat 2018",
    "Skoda Octavia 2019",
    "Hyundai Tucson 2021",
    "Kia Sportage 2020",
]

_CONTRACT_KINDS = [
    "КАСКО",
    "Имущество",
    "Грузы",
    "Страховка за границу",
    "Ответственность экспедитора",
    "CMR",
    "ДМС",
    "Другой: <корпоративный>",
]

_CURRENCIES = ["BYN", "USD", "EUR", "RUB", "CNY"]


@dataclass(frozen=True)
class GeneratedContract:
    contract_kind: str
    currency: str
    total_amount_minor: int
    payments: list[tuple[int, date]]
    start_date: date
    end_date: date
    vehicle_description: str | None


def _random_full_name() -> str:
    if random.random() < 0.5:
        first = random.choice(_NAMES_MALE)
        patronymic = random.choice(_PATRONYMICS_MALE)
    else:
        first = random.choice(_NAMES_FEMALE)
        patronymic = random.choice(_PATRONYMICS_FEMALE)
    last = random.choice(_LASTNAMES)
    return f"{first} {patronymic} {last}"


def _random_phone() -> str:
    # Rough Belarus-style format.
    return f"+375{random.choice(['29', '33', '44', '25'])}{random.randint(1000000, 9999999)}"


def _random_email(full_name: str) -> str:
    base = full_name.split()[-1].lower()
    suffix = random.randint(10, 9999)
    return f"{base}{suffix}@example.com"


def _split_amount(total_amount_minor: int, n_parts: int) -> list[int]:
    """
    Split an integer total into n parts (sum == total) using random weights.
    """
    weights = [random.randint(1, 100) for _ in range(n_parts)]
    s = sum(weights)
    parts = [(total_amount_minor * w) // s for w in weights]
    remainder = total_amount_minor - sum(parts)
    # remainder is always < n_parts when using integer floors above
    for _ in range(remainder):
        parts[random.randrange(n_parts)] += 1
    return parts


def _generate_payment_schedule() -> tuple[date, date, list[tuple[int, date]]]:
    start_date = date.today() + timedelta(days=random.randint(-20, 20))
    duration_days = random.randint(60, 240)
    end_date = start_date + timedelta(days=duration_days)

    n_parts = random.choice([1, 2, 2, 3, 3, 4])  # weighted towards 2-3

    # Total amount in "currency units * 100".
    total_amount_minor = random.randint(50_000, 5_000_000)

    amounts = _split_amount(total_amount_minor, n_parts)
    payments: list[tuple[int, date]] = []

    for i in range(n_parts):
        day_offset = round(duration_days * (i + 1) / n_parts)
        due = start_date + timedelta(days=day_offset)
        payments.append((amounts[i], due))

    payments[-1] = (payments[-1][0], end_date)
    return start_date, end_date, payments


def _generate_contract(currency: str, contract_kind: str) -> GeneratedContract:
    start_date, end_date, payments = _generate_payment_schedule()
    total_amount_minor = sum(a for a, _ in payments)
    vehicle_description = random.choice(_CARS) if contract_kind == "КАСКО" else None
    # Replace placeholder subtype for "Другой".
    if contract_kind.startswith("Другой:"):
        subtype = random.choice(["партнерский", "агентский", "корпоративный", "индивидуальный"])
        contract_kind = f"Другой: {subtype}"
    return GeneratedContract(
        contract_kind=contract_kind,
        currency=currency,
        total_amount_minor=total_amount_minor,
        payments=payments,
        start_date=start_date,
        end_date=end_date,
        vehicle_description=vehicle_description,
    )


async def _get_agents() -> list[User]:
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.role == UserRole.agent))
        return list(res.scalars().all())


async def main(count: int, per_agent: bool, seed: int | None) -> None:
    if seed is not None:
        random.seed(seed)

    await init_db()

    agents = await _get_agents()
    if not agents:
        raise SystemExit(
            "В БД нет пользователей с ролью agent. "
            "Сначала зайди в бота как агент (нажми 'Я агент'), чтобы создать пользователя-агента."
        )

    if per_agent:
        agents_to_seed = agents
        per_agent_count = count
    else:
        agents_to_seed = [agents[0]]
        per_agent_count = count

    print("Seed target agent_tg_ids:", [a.tg_id for a in agents_to_seed])

    created_clients = 0
    for agent in agents_to_seed:
        for i in range(per_agent_count):
            full_name = _random_full_name()
            phone = _random_phone()
            email = _random_email(full_name)

            client = await create_client(agent.tg_id, full_name, phone, email)

            contract_kind = random.choice(_CONTRACT_KINDS)
            currency = random.choice(_CURRENCIES)
            g = _generate_contract(currency=currency, contract_kind=contract_kind)

            company = random.choice(_COMPANIES)
            contract_number = f"{random.randint(10000, 99999)}-{i + 1}"

            await create_contract_for_client(
                agent_tg_id=agent.tg_id,
                client_id=client.id,
                contract_number=contract_number,
                company=company,
                contract_kind=g.contract_kind,
                start_date=g.start_date,
                end_date=g.end_date,
                total_amount_minor=g.total_amount_minor,
                insured_sum_minor=g.total_amount_minor * random.randint(50, 300),
                currency=g.currency,
                initial_payment_amount_minor=g.payments[0][0] if g.payments else 1000,
                initial_payment_due_date=g.start_date,
                payments=g.payments[1:] if len(g.payments) > 1 else [],
                vehicle_description=g.vehicle_description,
            )

            created_clients += 1

    print(f"Created clients: {created_clients}")


async def _seed_for_agent(agent_tg_id: int, count: int, seed: int | None) -> None:
    if seed is not None:
        random.seed(seed)

    await init_db()

    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res.scalar_one_or_none()
        if agent is None:
            session.add(User(tg_id=agent_tg_id, role=UserRole.agent))
            await session.commit()
            # We'll reload agent in the next block.

    # Reload agent to get correct ORM identity.
    async with get_session_maker()() as session:
        res = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent = res.scalar_one()

        print("Seed target agent_tg_id:", agent.tg_id)
        created_clients = 0
        for i in range(count):
            full_name = _random_full_name()
            phone = _random_phone()
            email = _random_email(full_name)

            client = await create_client(agent.tg_id, full_name, phone, email)

            contract_kind = random.choice(_CONTRACT_KINDS)
            currency = random.choice(_CURRENCIES)
            g = _generate_contract(currency=currency, contract_kind=contract_kind)

            company = random.choice(_COMPANIES)
            contract_number = f"{random.randint(10000, 99999)}-{i + 1}"

            await create_contract_for_client(
                agent_tg_id=agent.tg_id,
                client_id=client.id,
                contract_number=contract_number,
                company=company,
                contract_kind=g.contract_kind,
                start_date=g.start_date,
                end_date=g.end_date,
                total_amount_minor=g.total_amount_minor,
                insured_sum_minor=g.total_amount_minor * random.randint(50, 300),
                currency=g.currency,
                initial_payment_amount_minor=g.payments[0][0] if g.payments else 1000,
                initial_payment_due_date=g.start_date,
                payments=g.payments[1:] if len(g.payments) > 1 else [],
                vehicle_description=g.vehicle_description,
            )
            created_clients += 1

        print(f"Created clients: {created_clients}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20, help="Количество клиентов для генерации")
    parser.add_argument("--per-agent", action="store_true", help="Создавать count клиентов для КАЖДОГО агента")
    parser.add_argument("--agent-tg-id", type=int, default=None, help="Сидировать только для указанного agent tg_id")
    parser.add_argument("--seed", type=int, default=None, help="Фиксировать seed для воспроизводимости")
    args = parser.parse_args()

    if args.agent_tg_id is not None:
        asyncio.run(_seed_for_agent(args.agent_tg_id, count=args.count, seed=args.seed))
    else:
        asyncio.run(main(count=args.count, per_agent=args.per_agent, seed=args.seed))

