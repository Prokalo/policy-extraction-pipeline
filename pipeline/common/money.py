from __future__ import annotations

import copy
import re

from pipeline.common.text import deaccent, keytext_loose as keytext, norm


COMMON = "COMMON"


def money(raw):
    raw = norm(raw)
    if not raw:
        return {"raw_value": None, "amount": None, "currency": None}
    if not re.search(r"\d", raw):
        return {"raw_value": raw, "amount": None, "currency": None}
    match = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    amount = float(match.group(1).replace(",", "")) if match else None
    low = deaccent(raw).lower()
    if "dls" in low or "usd" in low or "dolar" in low:
        currency = "USD"
    elif "peso" in low or "mxn" in low:
        currency = "MXN"
    else:
        currency = None
    return {"raw_value": raw, "amount": amount, "currency": currency}


def coinsurance(raw):
    raw = norm(raw)
    if not raw:
        return {"raw_value": None, "percentage": None, "applies": None}
    low = deaccent(raw).lower()
    if "no aplica" in low:
        return {"raw_value": raw, "percentage": None, "applies": False}
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
    if match:
        return {"raw_value": raw, "percentage": float(match.group(1)), "applies": True}
    return {"raw_value": raw, "percentage": None, "applies": None}


def service_cost(raw):
    raw = norm(raw)
    if not raw:
        return {"raw_value": None, "amount": None, "currency": None, "unit": None}
    if "por servicio" not in deaccent(raw).lower():
        return {"raw_value": None, "amount": None, "currency": None, "unit": None}
    match = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    amount = float(match.group(1).replace(",", "")) if match else None
    return {"raw_value": raw, "amount": amount, "currency": "MXN", "unit": "por servicio"}


def empty_money():
    return {"raw_value": None, "amount": None, "currency": None}


def empty_coinsurance():
    return {"raw_value": None, "percentage": None, "applies": None}


def empty_service_cost():
    return {"raw_value": None, "amount": None, "currency": None, "unit": None}


def normalize_status(raw):
    raw = norm(raw)
    if "amparada" in deaccent(raw).lower():
        return "Amparada"
    return None


def make_policy_coverage(name, category, sum_raw=None, ded_raw=None, coins_raw=None, status=None):
    return {
        "category": category,
        "name": name,
        "scope": "Policy",
        "status": status,
        "sum_insured": money(sum_raw) if sum_raw else empty_money(),
        "deductible": money(ded_raw) if ded_raw else empty_money(),
        "coinsurance": coinsurance(coins_raw) if coins_raw else empty_coinsurance(),
        "service_cost": empty_service_cost(),
        "notes": None,
        "source_page": 1,
    }


def coverage_sig(row):
    def raw(field):
        return norm(((row.get(field) or {}).get("raw_value")))

    return (
        keytext(row.get("category")),
        keytext(row.get("name")),
        keytext(row.get("scope")),
        keytext(row.get("status")),
        raw("sum_insured"),
        raw("deductible"),
        raw("coinsurance"),
        raw("service_cost"),
    )


def rule_sig(rule):
    return (
        keytext(rule.get("criteria")),
        rule.get("amount"),
        rule.get("secondary_amount"),
        rule.get("currency"),
        rule.get("percentage"),
        rule.get("secondary_percentage"),
        keytext(rule.get("raw_value")),
    )


def merge_rules(target, incoming):
    seen = {rule_sig(rule) for rule in target.get("rules", [])}
    for rule in incoming.get("rules", []):
        sig = rule_sig(rule)
        if sig not in seen:
            target.setdefault("rules", []).append(copy.deepcopy(rule))
            seen.add(sig)


def merge_condition_provenance(target, incoming, insured=None):
    source_pages = []
    for value in [target.get("source_page"), *(target.get("source_pages") or []), incoming.get("source_page"), *((incoming.get("source_pages") or []))]:
        if isinstance(value, int) and value not in source_pages:
            source_pages.append(value)
    if source_pages:
        source_pages.sort()
        target["source_page"] = source_pages[0]
        if len(source_pages) > 1:
            target["source_pages"] = source_pages
        else:
            target.pop("source_pages", None)
    applies = [item for item in (target.get("applies_to_insured_numbers") or []) if isinstance(item, int)]
    for value in incoming.get("applies_to_insured_numbers") or []:
        if isinstance(value, int) and value not in applies:
            applies.append(value)
    if insured and insured.get("insured_number") is not None and insured["insured_number"] not in applies:
        applies.append(insured["insured_number"])
    if applies:
        applies.sort()
        target["applicability_scope"] = "Insured"
        target["applies_to_insured_numbers"] = applies


def make_rule(criteria, raw_value=None, amount=None, secondary_amount=None, currency=None, percentage=None, secondary_percentage=None, unit=None, effective_start_date=None, effective_end_date=None, notes=None):
    return {
        "criteria": criteria,
        "raw_value": raw_value,
        "amount": amount,
        "secondary_amount": secondary_amount,
        "currency": currency,
        "percentage": percentage,
        "secondary_percentage": secondary_percentage,
        "unit": unit,
        "effective_start_date": effective_start_date,
        "effective_end_date": effective_end_date,
        "notes": notes,
    }


def normalize_amount_rule(raw):
    raw = norm(raw)
    if not raw:
        return None
    parsed = money(raw)
    if parsed.get("amount") is None:
        return None
    amount_match = re.search(r"(\$\s*[\d,]+(?:\.\d+)?)", raw, re.I)
    amount_text = amount_match.group(1) if amount_match else raw
    currency = parsed.get("currency")
    if currency == "MXN":
        raw_value = f"{amount_text} pesos"
    elif currency == "USD":
        raw_value = raw
    else:
        raw_value = raw
    return parsed["amount"], currency, norm(raw_value)


def money_pat():
    return r"[\d,]+(?:\.\d+)?"


def parse_money(raw):
    match = re.search(r"([\d,]+(?:\.\d+)?)", raw or "")
    return float(match.group(1).replace(",", "")) if match else None


def parse_percentage(raw):
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", norm(raw), re.I)
    return float(match.group(1)) if match else None


def format_percentage(value):
    if value is None:
        return None
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:g}%"
