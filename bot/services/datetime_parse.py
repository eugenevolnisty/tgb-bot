from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass(frozen=True)
class DateParseResult:
    target_date: date
    normalized: str


WEEKDAYS = {
    "понедельник": 0,
    "пон": 0,
    "вторник": 1,
    "вт": 1,
    "среда": 2,
    "ср": 2,
    "четверг": 3,
    "чт": 3,
    "пятница": 4,
    "пт": 4,
    "суббота": 5,
    "сб": 5,
    "воскресенье": 6,
    "вс": 6,
}


def parse_date_ru(text: str, today: date) -> DateParseResult | None:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = t.removeprefix("в ").removeprefix("во ")

    if t in {"сегодня"}:
        return DateParseResult(target_date=today, normalized="сегодня")
    if t in {"завтра"}:
        return DateParseResult(target_date=today + timedelta(days=1), normalized="завтра")
    if t in {"послезавтра"}:
        return DateParseResult(target_date=today + timedelta(days=2), normalized="послезавтра")

    if t in WEEKDAYS:
        wd = WEEKDAYS[t]
        delta = (wd - today.weekday()) % 7
        if delta == 0:
            delta = 7
        d = today + timedelta(days=delta)
        return DateParseResult(target_date=d, normalized=f"в {t}")

    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", t)
    if m:
        dd, mm, yyyy = map(int, m.groups())
        try:
            return DateParseResult(target_date=date(yyyy, mm, dd), normalized=f"{dd:02d}.{mm:02d}.{yyyy}")
        except ValueError:
            return None

    m2 = re.fullmatch(r"(\d{1,2})\.(\d{1,2})", t)
    if m2:
        dd, mm = map(int, m2.groups())
        yyyy = today.year
        try:
            candidate = date(yyyy, mm, dd)
        except ValueError:
            return None
        if candidate < today:
            candidate = date(yyyy + 1, mm, dd)
        return DateParseResult(target_date=candidate, normalized=f"{dd:02d}.{mm:02d}.{candidate.year}")

    return None


def parse_time_ru(text: str) -> time | None:
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = t.removeprefix("в ").removeprefix("во ")
    compact = t.replace(" ", "")

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", compact)
    if m:
        hh, mm = map(int, m.groups())
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hour=hh, minute=mm)
        return None

    # "16 40"
    m_space = re.fullmatch(r"(\d{1,2}) (\d{2})", t)
    if m_space:
        hh, mm = map(int, m_space.groups())
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return time(hour=hh, minute=mm)

    # "5 вечера" / "5 утра" / "5 дня" / "5 ночи"
    m_words = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))? ?(утра|дня|вечера|ночи)", t)
    if m_words:
        hh = int(m_words.group(1))
        mm = int(m_words.group(2) or "00")
        part = m_words.group(3)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        if part == "утра":
            hh = 0 if hh == 12 else hh
        elif part in {"дня", "вечера"}:
            if 1 <= hh <= 11:
                hh += 12
        elif part == "ночи":
            if hh == 12:
                hh = 0
        if 0 <= hh <= 23:
            return time(hour=hh, minute=mm)

    m2 = re.fullmatch(r"(\d{1,2})", compact)
    if m2:
        hh = int(m2.group(1))
        if 0 <= hh <= 23:
            return time(hour=hh, minute=0)
    return None


def parse_relative_ru(text: str, now_local: datetime) -> datetime | None:
    """
    Parses: "через час", "через 2 минуты", "через 1 час 7 минут"
    Returns datetime in same tz as now_local.
    """
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    if not t.startswith("через "):
        return None
    tail = t.removeprefix("через ").strip()
    if tail in {"час", "часик"}:
        return now_local + timedelta(hours=1)

    hours = 0
    minutes = 0

    mh = re.search(r"(\d+)\s*(час|часа|часов)", tail)
    if mh:
        hours = int(mh.group(1))
    mm = re.search(r"(\d+)\s*(минута|минуты|минут)", tail)
    if mm:
        minutes = int(mm.group(1))

    if hours == 0 and minutes == 0:
        return None
    return now_local + timedelta(hours=hours, minutes=minutes)


def parse_duration_ru(text: str) -> timedelta | None:
    """
    Parses duration phrases without leading 'через', examples:
      - "5 минут"
      - "2 часа"
      - "1 час 7 минут"
      - "1ч 30м"
    """
    t = text.strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = t.removeprefix("через ").strip()

    if t in {"час", "часик"}:
        return timedelta(hours=1)

    # compact forms like "1ч30м"
    compact = t.replace(" ", "")
    m_comp = re.fullmatch(r"(?:(\d+)ч)?(?:(\d+)м)?", compact)
    if m_comp and (m_comp.group(1) or m_comp.group(2)):
        h = int(m_comp.group(1) or "0")
        m = int(m_comp.group(2) or "0")
        if h == 0 and m == 0:
            return None
        return timedelta(hours=h, minutes=m)

    hours = 0
    minutes = 0
    mh = re.search(r"(\d+)\s*(час|часа|часов)", t)
    if mh:
        hours = int(mh.group(1))
    mm = re.search(r"(\d+)\s*(минута|минуты|минут|мин)", t)
    if mm:
        minutes = int(mm.group(1))

    if hours == 0 and minutes == 0:
        # allow plain number == minutes
        m_plain = re.fullmatch(r"(\d+)", t)
        if m_plain:
            minutes = int(m_plain.group(1))
        else:
            return None

    return timedelta(hours=hours, minutes=minutes)


def combine_local(dt_date: date, dt_time: time, now_local: datetime) -> datetime:
    # Returns naive/zone-aware depending on now_local; caller can attach tz outside.
    return datetime.combine(dt_date, dt_time, tzinfo=now_local.tzinfo)

