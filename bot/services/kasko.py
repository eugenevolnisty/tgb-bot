from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class KaskoInput:
    brand_model: str
    year: int
    car_value: float
    abroad: bool
    drivers_count: int
    youngest_driver_age: int


@dataclass(frozen=True)
class KaskoQuote:
    premium: float
    currency: str
    breakdown: list[tuple[str, float]]


def _coef_car_age(car_age_years: int) -> float:
    if car_age_years <= 3:
        return 1.00
    if car_age_years <= 7:
        return 1.10
    return 1.25


def _coef_drivers_count(drivers_count: int) -> float:
    if drivers_count <= 1:
        return 1.00
    if drivers_count == 2:
        return 1.05
    return 1.10


def _coef_youngest_age(age: int) -> float:
    if age < 23:
        return 1.30
    if age < 30:
        return 1.15
    return 1.00


def calculate_kasko(inp: KaskoInput) -> KaskoQuote:
    """
    Тестовый расчёт КАСКО (коэффициенты можно заменить на реальные).
    """
    current_year = date.today().year
    car_age = max(0, current_year - inp.year)

    base_rate = 0.04  # 4% от стоимости авто в год (тест)
    c_age = _coef_car_age(car_age)
    c_drivers = _coef_drivers_count(inp.drivers_count)
    c_young = _coef_youngest_age(inp.youngest_driver_age)
    c_abroad = 1.10 if inp.abroad else 1.00

    premium = inp.car_value * base_rate * c_age * c_drivers * c_young * c_abroad
    premium = max(premium, 150.0)  # минимальная цена (тест)
    premium = round(premium, 2)

    breakdown = [
        ("Базовая ставка (4% от стоимости)", base_rate),
        (f"Коэффициент возраста авто (лет: {car_age})", c_age),
        (f"Коэффициент количества водителей (шт: {inp.drivers_count})", c_drivers),
        (f"Коэффициент минимального возраста водителя (лет: {inp.youngest_driver_age})", c_young),
        ("Коэффициент выезда за границу", c_abroad),
    ]
    return KaskoQuote(premium=premium, currency="BYN", breakdown=breakdown)

