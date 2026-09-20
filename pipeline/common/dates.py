from __future__ import annotations

import re
from datetime import date, datetime

from pipeline.common.text import deaccent, norm


COMMON = "COMMON"


DATE_FIELDS = {
    "issue_date",
    "coverage_start_date",
    "coverage_end_date",
    "birth_date",
    "seniority_date",
    "registration_date",
    "effective_start_date",
    "effective_end_date",
}


def parse_date(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    s = norm(str(value))
    if not s:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            date.fromisoformat(s)
            return s
        except ValueError:
            return value
    match = re.fullmatch(r"(\d{1,2})[\/\-\s](\d{1,2})[\/\-\s](\d{4})", s)
    if match:
        day, month, year = map(int, match.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return value
    return value


def normalize_dates_recursive(obj):
    changes = []
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if key in DATE_FIELDS and value is not None:
                new_value = parse_date(value)
                if new_value != value:
                    obj[key] = new_value
                    changes.append((key, value, new_value))
            else:
                changes.extend(normalize_dates_recursive(value))
    elif isinstance(obj, list):
        for item in obj:
            changes.extend(normalize_dates_recursive(item))
    return changes


def iso_date_es(day, month_name, year):
    months = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    month = months.get(deaccent(month_name).lower())
    if not month:
        return None
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def iso_ymd(day, month, year):
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
