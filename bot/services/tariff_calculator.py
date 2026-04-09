import json

from bot.db.models import DefaultTariff, TariffCard
from bot.db.repo import get_default_tariff, get_tariff_card


async def calculate_premium(
    tenant_id: int,
    insurance_type_id: int,
    type_key: str,
    params: dict,
    company_id: int | None = None,
) -> dict:
    """
    Рассчитать страховой взнос.
    """
    card = await get_tariff_card(tenant_id, insurance_type_id, company_id)
    source = "agent"

    # Fallback: specific company -> universal company -> default tariff.
    if card is None and company_id is not None:
        card = await get_tariff_card(tenant_id, insurance_type_id, None)

    if card is None:
        default = await get_default_tariff(type_key)
        if default is None:
            return {"error": "Тариф не настроен"}
        config = _load_config(default.config)
        card_type = default.card_type
        source = "default"
    else:
        config = _load_config(card.config)
        card_type = card.card_type

    calculators = {
        "percentage": _calc_percentage,
        "parametric": _calc_parametric,
        "table": _calc_table,
        "packages": _calc_packages,
        "matrix": _calc_matrix,
    }
    calc_func = calculators.get(card_type)
    if calc_func is None:
        return {"error": f"Неизвестный тип: {card_type}"}

    result = calc_func(config, params)
    if "error" not in result:
        result["card_type"] = card_type
        result["source"] = source
        result.setdefault("currency", "BYN")
    return result


async def calculate_for_all_companies(
    tenant_id: int,
    insurance_type_id: int,
    type_key: str,
    company_ids: list[int],
    params: dict,
) -> list[dict]:
    """
    Рассчитать взнос по всем компаниям агента для данного вида страхования.
    """
    variants: list[dict] = []
    variant_num = 1

    for company_id in company_ids:
        result = await calculate_premium(
            tenant_id=tenant_id,
            insurance_type_id=insurance_type_id,
            type_key=type_key,
            params=params,
            company_id=company_id,
        )
        if "error" in result:
            continue
        enriched = {
            "company_id": company_id,
            "variant_num": variant_num,
            **result,
        }
        variants.append(enriched)
        variant_num += 1

    if not variants:
        return [{"error": "Тариф не настроен"}]
    return variants


def _calc_percentage(config: dict, params: dict) -> dict:
    rate = float(config.get("rate", 0))
    min_premium = float(config.get("min_premium", 0))
    value = params.get("value")
    if value is None:
        return {"error": "Не указана страховая сумма"}
    value = float(value)

    premium = value * rate / 100
    premium = max(premium, min_premium)
    premium = round(premium, 2)
    details = f"{rate}% от {round(value, 2)} = {premium} BYN"
    return {"premium": premium, "details": details}


def _calc_parametric(config: dict, params: dict) -> dict:
    min_premium = float(config.get("min_premium", 0))
    lines: list[str] = []
    applied_coefficients: list[dict] = []

    base_rates = config.get("base_rates", {}) or {}
    cargo_type = params.get("cargo_type")

    if base_rates and cargo_type:
        base_rate_value = base_rates.get(cargo_type)
        if base_rate_value is None:
            return {"error": f"Неизвестная категория груза: {cargo_type}"}
        base_rate = float(base_rate_value)
        value = float(params.get("limit", 0))
        lines.append(f"Базовая ставка ({cargo_type}): {base_rate}%")
        lines.append(f"База расчёта (limit): {value}")
    else:
        base_rate = float(config.get("base_rate", 0))
        if params.get("value") is None:
            return {"error": "Не указана страховая сумма"}
        value = float(params["value"])
        lines.append(f"Базовая ставка: {base_rate}%")
        lines.append(f"База расчёта (value): {value}")

    premium = value * base_rate / 100
    lines.append(f"Базовый взнос: {round(premium, 2)} BYN")

    if "age_coefficients" in config and params.get("car_age") is not None:
        coeff = _find_coefficient(config.get("age_coefficients", {}), float(params["car_age"]))
        premium *= coeff
        applied_coefficients.append({"name": "age", "value": coeff, "param": params["car_age"]})
        lines.append(f"Коэф. возраста ({params['car_age']}): x{coeff}")

    if "deductible_discount" in config and params.get("deductible") is not None:
        coeff = _find_coefficient(config.get("deductible_discount", {}), float(params["deductible"]))
        premium *= coeff
        applied_coefficients.append({"name": "deductible", "value": coeff, "param": params["deductible"]})
        lines.append(f"Коэф. франшизы ({params['deductible']}): x{coeff}")

    if "limit_coefficients" in config and params.get("limit") is not None:
        coeff = _find_coefficient(config.get("limit_coefficients", {}), float(params["limit"]))
        premium *= coeff
        applied_coefficients.append({"name": "limit", "value": coeff, "param": params["limit"]})
        lines.append(f"Коэф. лимита ({params['limit']}): x{coeff}")

    if "vehicle_count_discount" in config and params.get("vehicle_count") is not None:
        coeff = _find_coefficient(
            config.get("vehicle_count_discount", {}),
            float(params["vehicle_count"]),
        )
        premium *= coeff
        applied_coefficients.append(
            {"name": "vehicle_count", "value": coeff, "param": params["vehicle_count"]}
        )
        lines.append(f"Коэф. количества ТС ({params['vehicle_count']}): x{coeff}")

    premium = max(premium, min_premium)
    premium = round(premium, 2)
    lines.append(f"Минимальный взнос: {min_premium} BYN")
    lines.append(f"Итого: {premium} BYN")
    return {
        "premium": premium,
        "details": "\n".join(lines),
        "applied_coefficients": applied_coefficients,
    }


def _calc_table(config: dict, params: dict) -> dict:
    rates = config.get("rates", {}) or {}
    category = params.get("category")
    if not category:
        return {
            "all_categories": list(rates.keys()),
            "details": "Выберите категорию",
        }
    if category not in rates:
        return {"error": f"Категория '{category}' не найдена"}

    premium = round(float(rates[category]), 2)
    return {
        "premium": premium,
        "category": category,
        "details": f"Категория: {category} -> {premium} BYN",
    }


def _calc_packages(config: dict, params: dict) -> dict:
    packages = config.get("packages", {}) or {}
    package_name = params.get("package")
    if not package_name:
        all_packages = {}
        for name, data in packages.items():
            all_packages[name] = {
                "price": float(data.get("price", 0)),
                "limit": data.get("limit"),
            }
        return {"all_packages": all_packages, "details": "Выберите пакет"}

    package_data = packages.get(package_name)
    if package_data is None:
        return {"error": f"Пакет '{package_name}' не найден"}

    premium = round(float(package_data.get("price", 0)), 2)
    limit = package_data.get("limit")
    description = package_data.get("description")
    lines = [f"Пакет «{package_name}»"]
    if limit:
        lines.append(f"Лимит: {limit}")
    if description:
        lines.append(str(description))
    lines.append(f"Цена: {premium} BYN")
    return {
        "premium": premium,
        "package": package_name,
        "limit": limit,
        "details": "\n".join(lines),
    }


def _calc_matrix(config: dict, params: dict) -> dict:
    zones = config.get("zones", {}) or {}
    zone = params.get("zone")
    if not zone:
        return {"all_zones": list(zones.keys()), "details": "Выберите зону"}

    days = params.get("days")
    if days is None:
        return {"error": "Укажите количество дней"}
    days = int(days)

    zone_data = zones.get(zone)
    if zone_data is None:
        return {"error": f"Зона '{zone}' не найдена"}

    variant = params.get("variant", "variant_A")
    variant_data = zone_data.get(variant)
    if variant_data is None:
        return {"error": f"Вариант '{variant}' не найден для зоны '{zone}'"}

    rate_per_day = _find_range_value(variant_data, days)
    if rate_per_day is None:
        return {"error": f"Тариф для срока {days} дней не найден"}

    premium = round(float(rate_per_day) * days, 2)
    details = (
        f"Зона: {zone}\n"
        f"Срок: {days} дней\n"
        f"Вариант: {variant}\n"
        f"Тариф: {float(rate_per_day)} BYN/день\n"
        f"Итого: {premium} BYN"
    )
    return {
        "premium": premium,
        "zone": zone,
        "days": days,
        "variant": variant,
        "rate_per_day": float(rate_per_day),
        "details": details,
    }


def _find_coefficient(coeffs: dict, value: int | float) -> float:
    found = _find_range_value(coeffs, value)
    if found is None:
        return 1.0
    return float(found)


def _find_range_value(ranges: dict, value: int | float) -> float | None:
    for key, range_value in ranges.items():
        key_str = str(key).strip()

        if "-" in key_str:
            start_str, end_str = key_str.split("-", 1)
            try:
                start = float(start_str)
                end = float(end_str)
            except ValueError:
                continue
            if start <= float(value) <= end:
                return float(range_value)
            continue

        if key_str.endswith("+"):
            lower_str = key_str[:-1]
            try:
                lower = float(lower_str)
            except ValueError:
                continue
            if float(value) >= lower:
                return float(range_value)
            continue

        try:
            exact = float(key_str)
        except ValueError:
            continue
        if float(value) == exact:
            return float(range_value)

    return None


def _load_config(raw_config: str) -> dict:
    try:
        loaded = json.loads(raw_config)
    except json.JSONDecodeError:
        return {}
    if isinstance(loaded, dict):
        return loaded
    return {}
