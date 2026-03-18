from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenericInput:
    full_name: str
    contact: str
    subject: str
    insured_value: float
    comment: str | None = None
    extra_type: str | None = None  # for "other"


@dataclass(frozen=True)
class GenericQuote:
    premium: float
    currency: str
    breakdown: list[tuple[str, float]]


def calculate_generic(kind: str, inp: GenericInput) -> GenericQuote:
    """
    Тестовые расчёты для видов:
    cargo / accident / cmr / dms / other
    """
    base_rates = {
        "cargo": 0.0035,     # 0.35%
        "accident": 0.0060,  # 0.60%
        "cmr": 0.0040,       # 0.40%
        "dms": 0.0100,       # 1.00%
        "other": 0.0050,     # 0.50%
    }
    base_rate = base_rates.get(kind, 0.0050)

    c_risk = 1.00
    if inp.comment:
        low = inp.comment.lower()
        if any(w in low for w in ["высокий риск", "дорого", "срочно"]):
            c_risk = 1.10
        if any(w in low for w in ["франшиза", "огранич"]):
            c_risk = max(c_risk, 1.05)

    premium = inp.insured_value * base_rate * c_risk
    min_premiums = {"cargo": 50.0, "accident": 20.0, "cmr": 60.0, "dms": 80.0, "other": 40.0}
    premium = max(premium, min_premiums.get(kind, 40.0))
    premium = round(premium, 2)

    breakdown = [
        (f"Базовая ставка ({base_rate*100:.2f}% от стоимости)", base_rate),
        ("Коэффициент рисков (по комментарию)", c_risk),
    ]
    return GenericQuote(premium=premium, currency="BYN", breakdown=breakdown)

