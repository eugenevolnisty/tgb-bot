from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExpeditorPlan:
    key: str
    title: str
    premium: float
    per_case_limit: int
    aggregate_limit: int
    franchise: int


PLANS: dict[str, ExpeditorPlan] = {
    "base": ExpeditorPlan(
        key="base",
        title="Базовый",
        premium=700.0,
        per_case_limit=50_000,
        aggregate_limit=250_000,
        franchise=500,
    ),
    "standard": ExpeditorPlan(
        key="standard",
        title="Стандарт",
        premium=1400.0,
        per_case_limit=100_000,
        aggregate_limit=500_000,
        franchise=1000,
    ),
    "premium": ExpeditorPlan(
        key="premium",
        title="Премиум",
        premium=2500.0,
        per_case_limit=250_000,
        aggregate_limit=750_000,
        franchise=2000,
    ),
    "maximum": ExpeditorPlan(
        key="maximum",
        title="Максимальный",
        premium=3500.0,
        per_case_limit=500_000,
        aggregate_limit=750_000,
        franchise=1500,
    ),
}


def parse_plan_choice(text: str) -> ExpeditorPlan | None:
    t = (text or "").strip().lower()
    aliases = {
        "1": "base",
        "базовый": "base",
        "base": "base",
        "2": "standard",
        "стандарт": "standard",
        "standard": "standard",
        "3": "premium",
        "премиум": "premium",
        "premium": "premium",
        "4": "maximum",
        "максимальный": "maximum",
        "maximum": "maximum",
    }
    key = aliases.get(t)
    if key is None:
        return None
    return PLANS[key]
