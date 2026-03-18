from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PropertyInput:
    full_name: str
    contact: str
    subject: str
    address_or_city: str
    property_value: float
    comment: str | None = None


@dataclass(frozen=True)
class PropertyQuote:
    premium: float
    currency: str
    breakdown: list[tuple[str, float]]


def calculate_property(inp: PropertyInput) -> PropertyQuote:
    """
    Тестовый расчёт страхования имущества.
    Логику/коэффициенты можно заменить на реальные правила.
    """
    base_rate = 0.0025  # 0.25% от стоимости (тест)
    # Простейший коэффициент "город/адрес": если Минск — чуть дороже (тест)
    c_location = 1.10 if "минск" in inp.address_or_city.lower() else 1.00
    # Если в комментарии есть "пожар"/"затоп" — чуть дороже (тест)
    c_risk = 1.05 if inp.comment and any(w in inp.comment.lower() for w in ["пожар", "затоп", "краж"]) else 1.00

    premium = inp.property_value * base_rate * c_location * c_risk
    premium = max(premium, 30.0)  # минимальная цена (тест)
    premium = round(premium, 2)

    breakdown = [
        ("Базовая ставка (0.25% от стоимости)", base_rate),
        ("Коэффициент локации", c_location),
        ("Коэффициент рисков (по комментарию)", c_risk),
    ]
    return PropertyQuote(premium=premium, currency="BYN", breakdown=breakdown)

