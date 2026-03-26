import argparse
import asyncio
import random
from datetime import date, timedelta

from sqlalchemy import select

from bot.db.base import get_session_maker, init_db
from bot.db.models import Client, Contract, PaymentStatus, User, UserRole
from bot.db.repo import create_contract_for_client


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
    "Другой: корпоративный",
]
_CURRENCIES = ["BYN", "USD", "EUR", "RUB", "CNY"]


def _random_contract_kind() -> str:
    return random.choice(_CONTRACT_KINDS)

async def _get_clients_for_agent(agent_tg_id: int) -> list[Client]:
    async with get_session_maker()() as session:
        agent = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent_user = agent.scalar_one_or_none()
        if agent_user is None:
            return []
        res = await session.execute(select(Client).where(Client.agent_user_id == agent_user.id))
        return list(res.scalars().all())


async def _count_pending_payments_for_dates(agent_tg_id: int, client_id: int, due_dates: list[date]) -> dict[date, int]:
    """
    Count pending payments for each due_date for one client.
    """
    from bot.db.models import Payment, Contract, PaymentStatus

    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent_user = res_u.scalar_one_or_none()
        if agent_user is None:
            return {d: 0 for d in due_dates}

        rows = await session.execute(
            select(Payment.due_date, Client.id)
            .select_from(Payment)
            .join(Contract, Contract.id == Payment.contract_id)
            .join(Client, Client.id == Contract.client_id)
            .where(
                Client.agent_user_id == agent_user.id,
                Client.id == client_id,
                Payment.status == PaymentStatus.pending,
                Payment.due_date.in_(due_dates),
            )
        )

        # rows contains (due_date, client.id) repeated for each row.
        counts: dict[date, int] = {d: 0 for d in due_dates}
        for due_d, _cid in rows.all():
            counts[due_d] += 1
        return counts


async def main(agent_tg_id: int, per_client_contracts: int, seed: int | None) -> None:
    if seed is not None:
        random.seed(seed)

    await init_db()
    async with get_session_maker()() as session:
        res_u = await session.execute(select(User).where(User.tg_id == agent_tg_id))
        agent_user = res_u.scalar_one_or_none()
        if agent_user is None:
            raise SystemExit("Agent tg_id not found in DB. Create agent user in bot first.")

    clients = await _get_clients_for_agent(agent_tg_id)
    if not clients:
        raise SystemExit("No clients for this agent.")

    today = date.today()
    due_dates = [today + timedelta(days=d) for d in (1, 3, 7)]

    # We'll always create two contracts (A: +1/+7, B: +3/+7).
    # If per_client_contracts > 2, we create extra mixed contracts.
    for idx, c in enumerate(clients):
        # Check if we already have anything due; if yes, skip to avoid duplicates.
        counts = await _count_pending_payments_for_dates(agent_tg_id, c.id, due_dates)
        if any(v > 0 for v in counts.values()):
            continue

        kind_a = _random_contract_kind()
        kind_b = _random_contract_kind()

        currency_a = random.choice(_CURRENCIES)
        currency_b = random.choice(_CURRENCIES)

        start_date = today - timedelta(days=30)
        end_date = today + timedelta(days=90)

        # Contract A: due +1 and +7
        total_a = random.randint(100_000, 2_000_000)
        part1_a = random.randint(30_000, total_a - 30_000)
        part7_a = total_a - part1_a
        payments_a = [(part1_a, due_dates[0]), (part7_a, due_dates[2])]
        initial_a = max(5_000, total_a // 10)
        total_a_with_initial = total_a + initial_a

        vehicle_desc_a = random.choice(_CARS) if kind_a == "КАСКО" else None
        await create_contract_for_client(
            agent_tg_id=agent_tg_id,
            client_id=c.id,
            contract_number=f"SEED-A-{today:%Y%m%d}-{idx+1}",
            company=random.choice(_COMPANIES),
            contract_kind=kind_a,
            start_date=start_date,
            end_date=end_date,
            total_amount_minor=total_a_with_initial,
            insured_sum_minor=total_a_with_initial * random.randint(50, 300),
            currency=currency_a,
            initial_payment_amount_minor=initial_a,
            initial_payment_due_date=start_date,
            payments=payments_a,
            vehicle_description=vehicle_desc_a,
        )

        # Contract B: due +3 and +7
        total_b = random.randint(120_000, 2_500_000)
        part3_b = random.randint(40_000, total_b - 40_000)
        part7_b = total_b - part3_b
        payments_b = [(part3_b, due_dates[1]), (part7_b, due_dates[2])]
        initial_b = max(5_000, total_b // 10)
        total_b_with_initial = total_b + initial_b

        vehicle_desc_b = random.choice(_CARS) if kind_b == "КАСКО" else None
        await create_contract_for_client(
            agent_tg_id=agent_tg_id,
            client_id=c.id,
            contract_number=f"SEED-B-{today:%Y%m%d}-{idx+1}",
            company=random.choice(_COMPANIES),
            contract_kind=kind_b,
            start_date=start_date,
            end_date=end_date,
            total_amount_minor=total_b_with_initial,
            insured_sum_minor=total_b_with_initial * random.randint(50, 300),
            currency=currency_b,
            initial_payment_amount_minor=initial_b,
            initial_payment_due_date=start_date,
            payments=payments_b,
            vehicle_description=vehicle_desc_b,
        )

        # Optional extra contracts if requested.
        if per_client_contracts > 2:
            for k in range(per_client_contracts - 2):
                kind_x = _random_contract_kind()
                currency_x = random.choice(_CURRENCIES)
                total_x = random.randint(80_000, 1_500_000)

                # Choose two due dates among +1/+3/+7.
                chosen = random.sample(due_dates, k=2)
                p1, p2 = random.sample([total_x // 2, total_x - (total_x // 2)], k=2)
                payments_x = [(p1, min(chosen)), (p2, max(chosen))]

                vehicle_desc_x = random.choice(_CARS) if kind_x == "КАСКО" else None
                initial_x = max(5_000, total_x // 10)
                total_x_with_initial = total_x + initial_x

                await create_contract_for_client(
                    agent_tg_id=agent_tg_id,
                    client_id=c.id,
                    contract_number=f"SEED-X{k+1}-{today:%Y%m%d}-{idx+1}",
                    company=random.choice(_COMPANIES),
                    contract_kind=kind_x,
                    start_date=start_date,
                    end_date=end_date,
                    total_amount_minor=total_x_with_initial,
                    insured_sum_minor=total_x_with_initial * random.randint(50, 300),
                    currency=currency_x,
                    initial_payment_amount_minor=initial_x,
                    initial_payment_due_date=start_date,
                    payments=payments_x,
                    vehicle_description=vehicle_desc_x,
                )

    print("Done seeding due payments.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-tg-id", type=int, required=True)
    parser.add_argument("--per-client-contracts", type=int, default=2, help="2 значит: A(+1/+7) и B(+3/+7)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    asyncio.run(main(agent_tg_id=args.agent_tg_id, per_client_contracts=args.per_client_contracts, seed=args.seed))

