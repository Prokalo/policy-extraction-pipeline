from __future__ import annotations

import copy
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

import pymupdf


CERTIFICATE_COVERAGES = [
    ("Nacional", "Básicas"),
    ("Asistencia en Viajes", "Básicas"),
    ("Membresía de Médica Móvil", "Básicas"),
    ("Enfermedades Catastróficas Nacional", "Básicas"),
    ("Emergencia Médica en el Extranjero", "Opcionales"),
    ("Cláusula Familiar", "Opcionales"),
    ("Cero Deducible por Accidente", "Opcionales"),
    ("Ampliación Hospitalaria Definida a PREMIUM", "Opcionales"),
]
COMMON = "COMMON"
GMM_SPECIFIC = "GMM-SPECIFIC"

# Function reuse inventory. COMMON functions are candidates for shared parser
# utilities; GMM-SPECIFIC functions depend on GMM/GNP/Premier layouts or rules.
FUNCTION_CLASSIFICATION = {
    "norm": COMMON,
    "deaccent": COMMON,
    "keytext": COMMON,
    "money": COMMON,
    "coinsurance": COMMON,
    "service_cost": COMMON,
    "empty_money": COMMON,
    "empty_coinsurance": COMMON,
    "empty_service_cost": COMMON,
    "normalize_status": COMMON,
    "group_visual_lines": COMMON,
    "find_certificate_region": GMM_SPECIFIC,
    "split_columns": GMM_SPECIFIC,
    "region_lines": GMM_SPECIFIC,
    "match_coverage_spans": GMM_SPECIFIC,
    "combine_span_cells": COMMON,
    "coverage_from_span": GMM_SPECIFIC,
    "extract_certificate_coverages": GMM_SPECIFIC,
    "page1_block": GMM_SPECIFIC,
    "make_policy_coverage": COMMON,
    "extract_policy_coverages": GMM_SPECIFIC,
    "detect_layout": GMM_SPECIFIC,
    "locate_insured_certificate_blocks": GMM_SPECIFIC,
    "coverage_sig": COMMON,
    "extract_certificate_coverages_from_block": GMM_SPECIFIC,
    "clean_old_coverage_warnings": GMM_SPECIFIC,
    "extract_insured_premium": GMM_SPECIFIC,
    "add_coverage_audit": GMM_SPECIFIC,
    "iso_date_es": COMMON,
    "canonical_type": GMM_SPECIFIC,
    "rule_sig": COMMON,
    "condition_sig": COMMON,
    "merge_rules": COMMON,
    "merge_condition_provenance": COMMON,
    "make_rule": COMMON,
    "normalize_amount_rule": COMMON,
    "normalize_amount_condition": GMM_SPECIFIC,
    "parse_waiting_period_rules": GMM_SPECIFIC,
    "page_for_insured_condition": GMM_SPECIFIC,
    "clean_policy_conditions": GMM_SPECIFIC,
    "rebuild_repeated_certificate_conditions": GMM_SPECIFIC,
    "_condition_page_texts": GMM_SPECIFIC,
    "_replace_condition_type": COMMON,
    "rebuild_additional_certificate_conditions": GMM_SPECIFIC,
    "detect_bold_condition_headings": GMM_SPECIFIC,
    "build_condition_heading_audit": GMM_SPECIFIC,
    "add_section": COMMON,
    "extract_document_sections_and_regulatory": GMM_SPECIFIC,
    "dedupe_sections": COMMON,
    "add_condition_audit": GMM_SPECIFIC,
    "iso_ymd": COMMON,
    "money_pat": COMMON,
    "parse_money": COMMON,
    "parse_percentage": COMMON,
    "format_percentage": COMMON,
    "resolve_premier_plan_family": GMM_SPECIFIC,
    "cell_text": COMMON,
    "find_doc_text": COMMON,
    "normalize_foreign_care_region": GMM_SPECIFIC,
    "is_foreign_care_table": GMM_SPECIFIC,
    "extract_foreign_care_matrix_from_doc": GMM_SPECIFIC,
    "foreign_care_rule": GMM_SPECIFIC,
    "rebuild_foreign_care_condition": GMM_SPECIFIC,
    "collapse_rebuilt_foreign_care_conditions": GMM_SPECIFIC,
    "extract_page1_metadata": GMM_SPECIFIC,
    "add_warning": COMMON,
    "remove_stale_metadata_warnings": GMM_SPECIFIC,
    "apply_metadata": GMM_SPECIFIC,
    "validate_metadata": GMM_SPECIFIC,
    "process_gnp_policy": GMM_SPECIFIC,
}


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def deaccent(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def keytext(s):
    return re.sub(r"[^a-z0-9%$]+", " ", deaccent(norm(s)).lower()).strip()


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


def group_visual_lines(page, tol=0.8):
    words = sorted(page.get_text("words"), key=lambda item: (item[1], item[0]))
    lines = []
    for word in words:
        y = word[1]
        target = None
        for line in reversed(lines[-4:]):
            if abs(line["y"] - y) <= tol:
                target = line
                break
        if target is None:
            target = {"y": y, "words": []}
            lines.append(target)
        target["words"].append(word)
        target["y"] = sum(item[1] for item in target["words"]) / len(target["words"])
    for line in lines:
        line["words"].sort(key=lambda item: item[0])
        line["text"] = norm(" ".join(item[4] for item in line["words"]))
    lines.sort(key=lambda item: item["y"])
    return lines


def find_certificate_region(page):
    lines = group_visual_lines(page)
    y_start = None
    y_end = None
    for line in lines:
        k = keytext(line["text"])
        if y_start is None and k in {"basicas", "basica"} and line["y"] > 220:
            y_start = line["y"]
            continue
        if y_start is not None and (
            "ver detalle de servicios" in k
            or k.startswith("este documento forma parte")
            or k.startswith("en caso de requerir mayor informacion")
        ):
            y_end = line["y"]
            break
    if y_start is None:
        raise RuntimeError("Could not locate certificate coverage region.")
    if y_end is None:
        y_end = page.rect.height - 40
    return y_start, y_end


def split_columns(line):
    cells = {"name": [], "sum": [], "ded": [], "coins": []}
    for word in line["words"]:
        x = word[0]
        if x >= 420:
            continue
        if x < 155:
            cells["name"].append(word[4])
        elif x < 270:
            cells["sum"].append(word[4])
        elif x < 365:
            cells["ded"].append(word[4])
        else:
            cells["coins"].append(word[4])
    return {key: norm(" ".join(value)) for key, value in cells.items()}


def region_lines(page):
    y_start, y_end = find_certificate_region(page)
    out = []
    for line in group_visual_lines(page):
        if not (y_start <= line["y"] < y_end):
            continue
        cells = split_columns(line)
        if any(cells.values()):
            out.append({"y": line["y"], **cells})
    return out


def match_coverage_spans(lines):
    spans = []
    i = 0
    while i < len(lines):
        name_key = keytext(lines[i]["name"])
        if name_key in {"basicas", "basica", "opcionales", "coberturas y servicios", "suma asegurada", "deducible", "coaseguro"}:
            i += 1
            continue
        best = None
        for width in (1, 2, 3):
            if i + width > len(lines):
                continue
            candidate = norm(" ".join(lines[j]["name"] for j in range(i, i + width)))
            candidate_key = keytext(candidate)
            for canonical, category in CERTIFICATE_COVERAGES:
                target = keytext(canonical)
                if candidate_key == target:
                    score = 1000 + len(target)
                elif target.startswith(candidate_key) and len(candidate_key) >= 5:
                    score = 100 + len(candidate_key)
                elif candidate_key.startswith(target) and len(target) >= 5:
                    score = 100 + len(target)
                else:
                    score = -1
                if score >= 0 and (best is None or score > best["score"]):
                    best = {"canonical": canonical, "category": category, "start": i, "end": i + width - 1, "score": score}
        if best and best["score"] >= 1000:
            spans.append(best)
            i = best["end"] + 1
        else:
            i += 1
    current_cat = "Básicas"
    for idx, line in enumerate(lines):
        nk = keytext(line["name"])
        if nk == "opcionales":
            current_cat = "Opcionales"
        elif nk in {"basicas", "basica"}:
            current_cat = "Básicas"
        for span in spans:
            if span["start"] == idx:
                span["category"] = current_cat
    return spans


def combine_span_cells(lines, span):
    chunk = lines[span["start"]: span["end"] + 1]
    return {
        "sum": norm(" ".join(item["sum"] for item in chunk if item["sum"])),
        "ded": norm(" ".join(item["ded"] for item in chunk if item["ded"])),
        "coins": norm(" ".join(item["coins"] for item in chunk if item["coins"])),
    }


def coverage_from_span(lines, span, source_page):
    vals = combine_span_cells(lines, span)
    sum_raw = vals["sum"]
    ded_raw = vals["ded"]
    coins_raw = vals["coins"]
    if keytext(ded_raw).endswith("no") and keytext(coins_raw) == "aplica":
        ded_raw = norm(re.sub(r"\bNo\s*$", "", ded_raw, flags=re.I))
        coins_raw = "No aplica"
    status = normalize_status(sum_raw)
    sum_obj = empty_money() if status else money(sum_raw)
    service_obj = service_cost(ded_raw)
    ded_obj = empty_money() if service_obj["raw_value"] is not None else money(ded_raw)
    return {
        "category": span["category"],
        "name": span["canonical"],
        "scope": "Nacional",
        "status": status,
        "sum_insured": sum_obj,
        "deductible": ded_obj,
        "coinsurance": coinsurance(coins_raw),
        "service_cost": service_obj,
        "notes": None,
        "source_page": source_page,
    }


def extract_certificate_coverages(page, page_no):
    lines = region_lines(page)
    spans = match_coverage_spans(lines)
    rows = [coverage_from_span(lines, span, page_no) for span in spans]
    order = {span["canonical"]: span["start"] for span in spans}
    rows.sort(key=lambda row: order[row["name"]])
    return rows


def page1_block(page):
    text = norm(page.get_text("text"))
    match = re.search(r"Coberturas y Servicios(.*?)(?:El círculo médico|El circulo medico)", text, flags=re.I)
    if not match:
        raise RuntimeError("Could not locate page-1 GNP policy coverage block.")
    return match.group(1)


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


def extract_policy_coverages(page):
    block = page1_block(page)
    rows = []
    match = re.search(r"(?:−|-)?\s*Nacional\s+((?:Sin Límite)|(?:[\d,]+(?:\.\d+)?\s+pesos))\s+([\d,]+(?:\.\d+)?\s+pesos)\s+(\d+(?:\.\d+)?\s*%)", block, flags=re.I)
    if match:
        rows.append(make_policy_coverage("Nacional", "Básicas", sum_raw=match.group(1), ded_raw=match.group(2), coins_raw=match.group(3)))
    match = re.search(r"Emergencia de gastos médicos\s+mayores no cubiertos\s+(?:−|-)?\s*Nacional\s+([\d,]+(?:\.\d+)?\s+pesos)\s+([\d,]+(?:\.\d+)?\s+pesos)\s+(\d+(?:\.\d+)?\s*%)", block, flags=re.I)
    if match:
        rows.append(make_policy_coverage("Emergencia de gastos médicos mayores no cubiertos - Nacional", "Básicas", sum_raw=match.group(1), ded_raw=match.group(2), coins_raw=match.group(3)))
    match = re.search(r"Emergencia Médica en el\s+Extranjero\s+([\d,]+(?:\.\d+)?\s+dls)\s+([\d,]+(?:\.\d+)?\s+dls)\s+(No aplica)", block, flags=re.I)
    if match:
        rows.append(make_policy_coverage("Emergencia Médica en el Extranjero", "Opcionales", sum_raw=match.group(1), ded_raw=match.group(2), coins_raw=match.group(3)))
    status_specs = [
        ("Asistencia en Viajes", "Básicas", r"Asistencia en Viajes\s+Amparada"),
        ("Membresía Médica Móvil", "Básicas", r"Membresía Médica Móvil\s+Amparada"),
        ("Enfermedades Catastróficas Nacional", "Básicas", r"Enfermedades Catastróficas\s+Nacional\s+Amparada"),
        ("Cero Deducible por Accidente", "Opcionales", r"Cero Deducible por Accidente\s+Amparada"),
        ("Ampliación Hospitalaria Definida a PREMIUM", "Opcionales", r"Ampliación Hospitalaria Definida a\s+PREMIUM\s+Amparada"),
    ]
    existing = {keytext(row["name"]) for row in rows}
    for name, category, pattern in status_specs:
        if keytext(name) in existing:
            continue
        if re.search(pattern, block, flags=re.I):
            rows.append(make_policy_coverage(name, category, status="Amparada"))
    return rows


def detect_layout(data, pdf):
    insurer = keytext(data.get("policy", {}).get("insurer"))
    if "grupo nacional provincial" not in insurer:
        raise RuntimeError("This parser only supports GNP policies.")
    plan = norm(data.get("policy", {}).get("plan_name") or data.get("policy", {}).get("plan_raw_text"))
    plan_key = keytext(plan)
    if "premier" in plan_key:
        return "gnp_premier"
    if "flexible" in plan_key or "ambar" in plan_key:
        return "gnp_flexible"
    page1 = keytext(pdf[0].get_text("text"))
    if "premier" in page1:
        return "gnp_premier"
    return "gnp_unknown"


def locate_insured_certificate_blocks(data, pdf):
    blocks = []
    for insured in data.get("insureds", []):
        page_numbers = []
        for page_no in insured.get("certificate_pages") or []:
            if isinstance(page_no, int) and 1 <= page_no <= len(pdf) and page_no not in page_numbers:
                page_numbers.append(page_no)
        if not page_numbers:
            page_no = insured.get("source_page")
            if isinstance(page_no, int) and 1 <= page_no <= len(pdf):
                page_numbers.append(page_no)
        if page_numbers:
            blocks.append((insured, page_numbers))
    if blocks:
        return blocks

    fallback = []
    for page_no, page in enumerate(pdf, start=1):
        match = re.search(r"Asegurado\s+(\d+)", page.get_text("text"), flags=re.I)
        if not match:
            continue
        insured_number = int(match.group(1))
        insured = next((item for item in data.get("insureds", []) if item.get("insured_number") == insured_number), None)
        if insured:
            fallback.append((insured, [page_no]))
    return fallback


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


def extract_certificate_coverages_from_block(pdf, page_numbers):
    merged = []
    by_sig = {}
    for page_no in page_numbers:
        try:
            rows = extract_certificate_coverages(pdf[page_no - 1], page_no)
        except Exception:
            continue
        for row in rows:
            sig = coverage_sig(row)
            existing = by_sig.get(sig)
            if existing is None:
                row["source_pages"] = [page_no]
                by_sig[sig] = row
                merged.append(row)
                continue
            pages = existing.setdefault("source_pages", [existing.get("source_page")])
            if page_no not in pages:
                pages.append(page_no)
                pages.sort()
    return merged


def clean_old_coverage_warnings(data):
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    cleaned = []
    removed = 0
    for warning in warnings:
        field = warning.get("field")
        issue = keytext(warning.get("issue"))
        repaired_field = field in {"insureds.coverages", "policy_coverages"} or str(field or "").startswith("coverage_")
        if repaired_field and ("coverage" in issue or "cobertura" in issue or "row association" in issue):
            removed += 1
            continue
        cleaned.append(warning)
    data["validation"]["warnings"] = cleaned
    return removed


def extract_insured_premium(page):
    """Read the labeled premium block from one insured certificate page."""
    text = page.get_text("text")
    match = re.search(r"Prima del Asegurado(.*?)(?:Vigencia de la versi[oó]n|Coberturas y Servicios)", text, re.I | re.S)
    block = match.group(1) if match else text
    patterns = {
        "net_premium": rf"Prima Neta\s+({money_pat()})",
        "installment_surcharge": rf"Recargo por Pago\s+Fraccionado\s+({money_pat()})",
        "policy_fee": rf"Derecho de P[oó]liza\s+({money_pat()})",
        "tax_amount": rf"I\.V\.A\.\s*16%\s+({money_pat()})",
        "total_amount": rf"Importe Total a\s+Pagar\s+({money_pat()})",
    }
    result = {}
    for field, pattern in patterns.items():
        value = re.search(pattern, block, re.I)
        result[field] = parse_money(value.group(1)) if value else None
    return result


def add_coverage_audit(data, layout, insured_counts, policy_count):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": "coverages",
        "issue": f"GNP deterministic coverage parser v2 used layout '{layout}'. Insured coverage counts={insured_counts}; policy-level coverage count={policy_count}.",
        "severity": "low",
        "source_page": None,
    })


def iso_date_es(day, month_name, year):
    months = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
        "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    month = months.get(deaccent(month_name).lower())
    if not month:
        return None
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def canonical_type(raw):
    aliases = {
        "periodo de cobertura": "Cobertura de preexistencia",
        "suma asegurada por periodo de cobertura": "Cobertura de preexistencia",
        "cobertura de preexistencia": "Cobertura de preexistencia",
        "region y coaseguro": "Cobertura de atención en el extranjero",
        "cobertura atencion en el extranjero": "Cobertura de atención en el extranjero",
        "cobertura de atencion en el extranjero": "Cobertura de atención en el extranjero",
        "tope de coaseguro": "Tope de coaseguro",
        "monto para productos de terapia genica": "Monto para Productos de Terapia génica",
        "eliminacion o reduccion de periodos de espera": "Eliminación o reducción de periodos de espera",
    }
    return aliases.get(keytext(raw), norm(raw) or "Otra condición")


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


def condition_sig(cond):
    return (
        canonical_type(cond.get("condition_type")),
        keytext(cond.get("scope")),
        keytext(cond.get("description")),
        # Optional numeric fields can be None or numbers; repr gives a stable
        # deterministic order without comparing unlike Python scalar types.
        tuple(sorted((rule_sig(rule) for rule in cond.get("rules", [])), key=repr)),
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


def normalize_amount_condition(cond):
    if cond.get("rules"):
        return 0
    description = norm(cond.get("description"))
    if not description:
        return 0
    match = re.search(r"\b(Monto(?:\s+m[aá]ximo\s+a\s+pagar)?)\s*:?\s*(\$\s*[\d,]+(?:\.\d+)?(?:\s*(?:MXN|pesos?))?)", description, flags=re.I)
    if not match:
        return 0
    criteria = norm(match.group(1))
    normalized = normalize_amount_rule(match.group(2))
    if not normalized:
        return 0
    amount, currency, raw_value = normalized
    cond["description"] = criteria
    cond["rules"] = [
        make_rule(
            criteria=criteria,
            raw_value=raw_value,
            amount=amount,
            currency=currency,
        )
    ]
    return 1


def parse_waiting_period_rules(cond):
    scope_text = norm(cond.get("scope"))
    desc_text = norm(cond.get("description"))
    text = " ".join(filter(None, [scope_text, desc_text]))
    rules = []

    coverage_match = re.search(
        r"cobertura de gastos m[eé]dicos mayores.*?(\d{1,2}/\d{1,2}/\d{4})\s*al:?\s*(\d{1,2}/\d{1,2}/\d{4})",
        text,
        flags=re.I,
    )
    if coverage_match:
        start_raw, end_raw = coverage_match.groups()
        rules.append(
            make_rule(
                criteria="Cobertura previa de gastos médicos mayores",
                raw_value=f"{start_raw} al {end_raw}",
                effective_start_date=start_raw,
                effective_end_date=end_raw,
            )
        )

    benefit_raw = None
    for rule in cond.get("rules", []):
        candidate = " ".join(filter(None, [norm(rule.get("raw_value")), norm(rule.get("criteria"))]))
        date_match = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", candidate)
        if date_match:
            benefit_raw = date_match.group(1)
            break
    if not benefit_raw:
        dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", desc_text)
        if dates:
            benefit_raw = dates[-1]
    if benefit_raw:
        rules.append(
            make_rule(
                criteria="Fecha considerada para el beneficio",
                raw_value=benefit_raw,
                effective_start_date=benefit_raw,
                effective_end_date=None,
            )
        )

    benefit_desc = scope_text
    if benefit_desc:
        benefit_desc = re.sub(r",?\s*tomando en consideraci[oó]n.*$", "", benefit_desc, flags=re.I)
    description_parts = [part for part in [benefit_desc, desc_text] if part]
    if description_parts:
        deduped = []
        for part in description_parts:
            if part not in deduped:
                deduped.append(part)
        cond["description"] = " ".join(deduped)

    if rules:
        cond["rules"] = rules
        cond["scope"] = "Insured"
        return 1
    return 0


def page_for_insured_condition(cond, insureds):
    applies = [item for item in cond.get("applies_to_insured_numbers", []) if isinstance(item, int)]
    if len(applies) == 1:
        target_number = applies[0]
        insured = next((item for item in insureds if item.get("insured_number") == target_number), None)
        if insured is not None:
            return insured
    source_page = cond.get("source_page")
    if not isinstance(source_page, int):
        return None
    candidates = []
    for insured in insureds:
        insured_page = insured.get("source_page")
        if isinstance(insured_page, int) and 0 < source_page - insured_page <= 2:
            candidates.append((source_page - insured_page, insured))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def clean_policy_conditions(data):
    raw = data.get("policy_conditions", [])
    policy = []
    by_key = {}
    moved_to_insured = 0
    normalized_amounts = 0
    normalized_waiting = 0
    for cond in raw:
        cond = copy.deepcopy(cond)
        ctype = canonical_type(cond.get("condition_type"))
        cond["condition_type"] = ctype
        insured = page_for_insured_condition(cond, data.get("insureds", []))
        if insured is not None:
            merge_condition_provenance(cond, cond, insured)
        normalized_amounts += normalize_amount_condition(cond)
        if ctype == "Eliminación o reducción de periodos de espera":
            normalized_waiting += parse_waiting_period_rules(cond)
            insured = page_for_insured_condition(cond, data.get("insureds", []))
            if insured is not None:
                insured.setdefault("conditions", [])
                insured["conditions"] = [item for item in insured["conditions"] if canonical_type(item.get("condition_type")) != ctype]
                merge_condition_provenance(cond, cond, insured)
                insured["conditions"].append(cond)
                moved_to_insured += 1
                continue
        desc_key = keytext(cond.get("description"))
        if ctype == "Cobertura de preexistencia" and "no aplica para premier 400" in desc_key and len(cond.get("rules", [])) <= 1:
            cond["condition_type"] = "Cobertura de atención en el extranjero"
            ctype = cond["condition_type"]
        sig = condition_sig(cond)
        if sig not in by_key:
            by_key[sig] = cond
            policy.append(cond)
        else:
            target = by_key[sig]
            merge_rules(target, cond)
            merge_condition_provenance(target, cond, insured)
            d1 = norm(target.get("description"))
            d2 = norm(cond.get("description"))
            if d2 and d2 not in d1:
                target["description"] = norm((d1 + " " + d2).strip())
    data["policy_conditions"] = policy
    return len(raw), len(policy), moved_to_insured, normalized_amounts, normalized_waiting


def rebuild_repeated_certificate_conditions(data, pdf, insured_blocks):
    """Rebuild repeated GNP certificate tables from their printed text.

    These conditions recur for every discovered insured. Parsing one canonical
    copy and attaching all observed pages avoids model-dependent merge splits.
    """
    insured_numbers = [
        insured.get("insured_number")
        for insured, _pages in insured_blocks
        if isinstance(insured.get("insured_number"), int)
    ]
    if not insured_numbers:
        return 0

    page_texts = {}
    for _insured, page_numbers in insured_blocks:
        for page_no in page_numbers:
            page_texts[page_no] = norm(pdf[page_no - 1].get_text("text"))

    specs = {
        "Cobertura de preexistencia": "cobertura de preexistencia",
        "Cobertura de atención en el extranjero": "cobertura atencion en el extranjero",
        "Tope de coaseguro": "tope de coaseguro",
        "Monto para Productos de Terapia génica": "monto para productos de terapia genica",
        "Auxiliares mecánicos electrónicos y/o computarizados": "auxiliares mecanicos electronicos y o computarizados",
    }
    pages_by_type = {
        condition_type: [page for page, text in page_texts.items() if heading in keytext(text)]
        for condition_type, heading in specs.items()
    }

    rebuilt = []
    for condition_type, source_pages in pages_by_type.items():
        if not source_pages:
            continue
        text = page_texts[source_pages[0]]
        rules = []
        if condition_type == "Cobertura de preexistencia":
            for criteria, amount in re.findall(r"(\d+\s*(?:[-–−]|a)\s*\d+\s*años|\d+\s*años\s+en\s+adelante)\s+\$?\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I):
                rules.append(make_rule(norm(criteria), f"${amount} pesos", parse_money(amount), currency="MXN"))
        elif condition_type == "Cobertura de atención en el extranjero":
            for plan, first, rest in re.findall(r"(Premium|Platino|[ÍI]ndigo|[ÁA]mbar|Cuarzo)\s+(\d+(?:\.\d+)?)%\s+(\d+(?:\.\d+)?)%", text, re.I):
                rules.append(make_rule(plan, f"{first}% / {rest}%", percentage=float(first), secondary_percentage=float(rest), unit="%"))
        elif condition_type == "Tope de coaseguro":
            for percentages, amount in re.findall(r"(\d+%\s*y\s*\d+%)\s+\$?\s*([\d,]+(?:\.\d+)?)", text, re.I):
                rules.append(make_rule(norm(percentages), f"${amount}", parse_money(amount), currency="MXN"))
        elif condition_type == "Monto para Productos de Terapia génica":
            match = re.search(r"Monto para Productos de Terapia g[eé]nica.*?\$\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I)
            if match:
                amount = match.group(1)
                rules.append(make_rule("Monto", f"$ {amount} pesos", parse_money(amount), currency="MXN"))
        elif condition_type == "Auxiliares mecánicos electrónicos y/o computarizados":
            match = re.search(r"Auxiliares mec[aá]nicos electr[oó]nicos y/o computarizados.*?Monto m[aá]ximo a pagar\s+\$\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I)
            if match:
                amount = match.group(1)
                rules.append(make_rule("Monto máximo a pagar", f"$ {amount} pesos", parse_money(amount), currency="MXN"))
        if not rules:
            continue
        rebuilt.append({
            "condition_type": condition_type,
            "scope": "Todos los asegurados",
            "description": None,
            "rules": rules,
            "source_page": min(source_pages),
            "source_pages": sorted(source_pages),
            "applicability_scope": "Insured",
            "applies_to_insured_numbers": sorted(set(insured_numbers)),
        })

    rebuilt_types = {item["condition_type"] for item in rebuilt}
    kept = [
        condition for condition in data.get("policy_conditions", [])
        if canonical_type(condition.get("condition_type")) not in rebuilt_types
    ]
    data["policy_conditions"] = kept + rebuilt
    return len(rebuilt)


def _condition_page_texts(pdf, insured_blocks):
    pages = {}
    page_to_insured = {}
    for insured, page_numbers in insured_blocks:
        header_page = insured.get("source_page")
        for page_no in page_numbers:
            if isinstance(header_page, int) and page_no <= header_page:
                continue
            pages[page_no] = norm(pdf[page_no - 1].get_text("text"))
            page_to_insured[page_no] = insured.get("insured_number")
    return pages, page_to_insured


def _replace_condition_type(data, condition):
    target = keytext(condition["condition_type"])
    data["policy_conditions"] = [
        item for item in data.get("policy_conditions", [])
        if keytext(item.get("condition_type")) != target
    ]
    data["policy_conditions"].append(condition)


def rebuild_additional_certificate_conditions(data, pdf, insured_blocks):
    """Deterministically capture condition families previously swallowed as prose."""
    page_texts, page_to_insured = _condition_page_texts(pdf, insured_blocks)
    rebuilt = 0

    def base(condition_type, pages, rules, description=None):
        insured_numbers = sorted({page_to_insured[p] for p in pages if isinstance(page_to_insured.get(p), int)})
        return {
            "condition_type": condition_type,
            "scope": "Asegurados indicados",
            "description": description,
            "rules": rules,
            "source_page": min(pages),
            "source_pages": sorted(pages),
            "applicability_scope": "Insured",
            "applies_to_insured_numbers": insured_numbers,
        }

    hospital_pages = [p for p, text in page_texts.items() if "penalizacion por acceso a hospitales de nivel superior" in keytext(text)]
    if hospital_pages:
        text = page_texts[hospital_pages[0]]
        points = re.search(r"(\d+(?:\.\d+)?)\s+puntos porcentuales por cada nivel hospitalario", text, re.I)
        cap = re.search(r"nivel inmediato superior.*?\$\s*([\d,]+(?:\.\d+)?)", text, re.I)
        rules = []
        if points:
            rules.append(make_rule("Penalización por cada nivel hospitalario que ascienda", points.group(0), percentage=float(points.group(1)), unit="puntos porcentuales"))
        if cap:
            rules.append(make_rule("Tope por atención en nivel inmediato superior", f"${cap.group(1)}", amount=parse_money(cap.group(1)), currency="MXN"))
        if rules:
            _replace_condition_type(data, base("Penalización por acceso a hospitales de nivel superior al contratado", hospital_pages, rules))
            rebuilt += 1

    device_phrase = "compra o renta de aparatos ortopedicos protesis y dispositivos medicos"
    device_pages = [p for p, text in page_texts.items() if device_phrase in keytext(text)]
    if device_pages:
        text = page_texts[device_pages[0]]
        prosthesis = re.search(r"Monto para pr[oó]tesis\s+\$\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I)
        device = re.search(r"Monto para dispositivo m[eé]dico o aparato ortop[eé]dico\s+\$\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I)
        rules = []
        if prosthesis:
            rules.append(make_rule("Monto para prótesis", f"${prosthesis.group(1)} pesos", amount=parse_money(prosthesis.group(1)), currency="MXN", unit="por aparato o prótesis"))
        if device:
            rules.append(make_rule("Monto para dispositivo médico o aparato ortopédico", f"${device.group(1)} pesos", amount=parse_money(device.group(1)), currency="MXN", unit="por aparato o dispositivo"))
        if rules:
            description = "Aplica por cada aparato ortopédico, prótesis o dispositivo médico que el asegurado requiera."
            _replace_condition_type(data, base("Compra o renta de aparatos ortopédicos, prótesis y dispositivos médicos", device_pages, rules, description))
            rebuilt += 1

    maternity_pages = [p for p, text in page_texts.items() if "ayuda para maternidad" in keytext(text)]
    if maternity_pages:
        text = page_texts[maternity_pages[0]]
        amount = re.search(r"Ayuda para maternidad.*?Suma Asegurada de Parto Normal o Ces[aá]rea:\s*([\d,]+(?:\.\d+)?)\s*pesos", text, re.I)
        if amount:
            rules = [make_rule("Suma asegurada de parto normal o cesárea", f"{amount.group(1)} pesos", amount=parse_money(amount.group(1)), currency="MXN")]
            _replace_condition_type(data, base("Ayuda para maternidad", maternity_pages, rules))
            rebuilt += 1
    return rebuilt


CONDITION_LAYOUT_LABELS = {
    "poliza de seguro gastos medicos", "linea azul poliza no", "version",
    "certificado de cobertura por asegurado", "condiciones especiales",
    "periodo de cobertura", "suma asegurada", "primeros", "100 000 pesos",
    "resto del", "gasto", "coaseguro contratado", "tope de coaseguro",
    "nacional", "monto", "monto maximo a pagar",
}


def detect_bold_condition_headings(page):
    """Return meaningful bold headings/subheadings in the certificate body."""
    headings = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            if line.get("bbox", [0, 0, 0, 0])[1] < 82 or line.get("bbox", [0, 0, 0, 0])[1] > 690:
                continue
            spans = [span for span in line.get("spans", []) if norm(span.get("text"))]
            if not spans or not all((span.get("flags", 0) & 16) or "black" in str(span.get("font", "")).lower() or "bold" in str(span.get("font", "")).lower() for span in spans):
                continue
            text = norm(" ".join(span.get("text", "") for span in spans))
            text = re.sub(r"^[\-–−•]+\s*", "", text)
            keyed = keytext(text)
            if not keyed or keyed in CONDITION_LAYOUT_LABELS:
                continue
            if text[:1].islower():
                continue
            if keyed.startswith(("pagina ", "en caso de requerir mayor informacion", "gnp al 55")):
                continue
            if re.fullmatch(r"[\d\s$%,.]+", text) or re.match(r"^\$?\s*[\d,]+(?:\.\d+)?\s+(?:pesos|dls)\b", text, re.I):
                continue
            headings.append(text)
    return list(dict.fromkeys(headings))


def build_condition_heading_audit(data, pdf, insured_blocks):
    """Classify every detected heading as structured, document text, or unmapped."""
    page_texts, page_to_insured = _condition_page_texts(pdf, insured_blocks)
    records = []
    for page_no in sorted(page_texts):
        detected = detect_bold_condition_headings(pdf[page_no - 1])
        if not detected:
            continue
        insured_number = page_to_insured.get(page_no)
        applicable = []
        for condition in data.get("policy_conditions", []):
            applies = condition.get("applies_to_insured_numbers") or []
            if insured_number in applies or not applies:
                applicable.append(condition)
        insured = next((item for item in data.get("insureds", []) if item.get("insured_number") == insured_number), None)
        if insured:
            applicable.extend(insured.get("conditions") or [])
        structured_keys = []
        for condition in applicable:
            structured_keys.append(keytext(condition.get("condition_type")))
            structured_keys.extend(keytext(rule.get("criteria")) for rule in condition.get("rules", []))

        mapped, document_text, unmapped = [], [], []
        for heading in detected:
            heading_key = keytext(heading)
            heading_variants = {heading_key, keytext(canonical_type(heading))}
            if any(
                variant == key or (len(variant) >= 12 and len(key) >= 12 and (variant in key or key in variant))
                for variant in heading_variants for key in structured_keys if key
            ):
                mapped.append(heading)
            elif len(heading) > 80 or heading.endswith("."):
                document_text.append(heading)
                add_section(data, "condition_document_text", heading, heading, page_no)
            else:
                unmapped.append(heading)
        records.append({
            "page": page_no,
            "insured_number": insured_number,
            "detected_headings": detected,
            "mapped_headings": mapped,
            "document_text_headings": document_text,
            "unmapped_headings": unmapped,
        })
    data["condition_heading_audit"] = records
    return records


def add_section(data, section_type, heading, text, page):
    text = norm(text)
    if not text:
        return False
    existing = data.setdefault("document_sections", [])
    sig = (section_type, keytext(text)[:500])
    for section in existing:
        if (section.get("section_type"), keytext(section.get("text"))[:500]) == sig:
            return False
    existing.append({"section_type": section_type, "heading": heading, "text": text, "page_start": page, "page_end": page})
    return True


def extract_document_sections_and_regulatory(data, pdf):
    added = 0
    reg_found = False
    full_pages = [(i + 1, norm(page.get_text("text"))) for i, page in enumerate(pdf)]
    for page_no, text in full_pages:
        match = re.search(r"(Este documento forma parte integrante del Contrato de Seguro.*?(?:Usuarios de Servicios Financieros\.|CONDUSEF\.))", text, flags=re.I)
        if match:
            added += add_section(data, "coverage_scope", "Alcance y documentos del contrato", match.group(1), page_no)
        match = re.search(r"(El tratamiento de los datos personales.*?(?:55\s*5227[−\- ]?9000\.?))", text, flags=re.I)
        if match:
            added += add_section(data, "privacy_notice", "Aviso de privacidad", match.group(1), page_no)
        match = re.search(r"(Para cualquier aclaración o duda no resuelta relacionada con su seguro.*?(?:condusef\.gob\.mx\.?))", text, flags=re.I)
        if match:
            added += add_section(data, "dispute_resolution", "UNE / CONDUSEF", match.group(1), page_no)
        match = re.search(r"(Los Certificados de todos y cada uno de los Asegurados.*?(?:cada Asegurado\.))", text, flags=re.I)
        if match:
            added += add_section(data, "document_delivery", "Entrega de certificados", match.group(1), page_no)
        reg_match = re.search(r"registradas ante la Comisión Nacional de Seguros y Fianzas.*?(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+de\s+(\d{4}).*?(CNSF[−\-][A-Z0-9−\-/]+(?:/CONDUSEF[−\-][A-Z0-9−\-]+)?)", text, flags=re.I)
        if reg_match:
            day, month, year, number = reg_match.groups()
            number = number.replace("−", "-")
            data["regulatory"] = {"registration_number": number, "registration_date": iso_date_es(day, month, year), "source_page": page_no}
            reg_found = True
            add_section(data, "regulatory_registration", "Registro CNSF", reg_match.group(0), page_no)
    return added, reg_found


def dedupe_sections(data):
    seen = set()
    out = []
    for section in data.get("document_sections", []):
        sig = (section.get("section_type"), keytext(section.get("text"))[:700])
        if sig not in seen:
            seen.add(sig)
            out.append(section)
    data["document_sections"] = out


def add_condition_audit(data, raw_count, final_count, moved, sections, reg_found):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": "policy_conditions",
        "issue": f"GNP condition cleanup v2: {raw_count} raw conditions -> {final_count} policy-wide conditions; {moved} routed to insured.conditions; {sections} document sections added; regulatory registration found={reg_found}.",
        "severity": "low",
        "source_page": None,
    })


def iso_ymd(day, month, year):
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


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


def resolve_premier_plan_family(data):
    plan_text = norm(data.get("policy", {}).get("plan_name") or data.get("policy", {}).get("plan_raw_text"))
    match = re.search(r"\bpremier\s*(100|200|300|400)\b", plan_text, re.I)
    if not match:
        return None
    return f"Premier {match.group(1)}"


def cell_text(cell):
    if isinstance(cell, dict):
        return norm(cell.get("text"))
    return norm(str(cell))


def find_doc_text(doc, cref):
    for item in doc.get("texts", []):
        if item.get("self_ref") == cref:
            return norm(item.get("text") or item.get("orig"))
    return None


def normalize_foreign_care_region(raw):
    return norm(re.sub(r"\s*\(\d+\)\s*$", "", raw or ""))


def is_foreign_care_table(table):
    grid = ((table.get("data") or {}).get("grid") or [])
    if len(grid) < 3:
        return False
    first = [keytext(cell_text(cell)) for cell in grid[0]]
    second = [keytext(cell_text(cell)) for cell in grid[1]]
    return (
        any(item.startswith("premier 100") for item in first)
        and any(item.startswith("premier 200") for item in first)
        and any(item.startswith("premier 300") for item in first)
        and second
        and second[0] == "region"
        and sum(1 for item in second[1:] if "primeros" in item and "100 000 pesos" in item) >= 3
        and sum(1 for item in second[1:] if item == "resto del gasto") >= 3
    )


def extract_foreign_care_matrix_from_doc(doc):
    for table in doc.get("tables", []):
        if not is_foreign_care_table(table):
            continue

        grid = (table.get("data") or {}).get("grid") or []
        top_row = grid[0]
        header_row = grid[1]
        column_plans = {}
        column_kinds = {}
        for col_idx, cell in enumerate(top_row):
            match = re.search(r"\bpremier\s*(100|200|300)\b", cell_text(cell), re.I)
            if match:
                column_plans[col_idx] = f"Premier {match.group(1)}"
        for col_idx, cell in enumerate(header_row):
            header = keytext(cell_text(cell))
            if "primeros" in header and "100 000 pesos" in header:
                column_kinds[col_idx] = "first_100k_percentage"
            elif header == "resto del gasto":
                column_kinds[col_idx] = "remaining_expense_percentage"

        matrix = []
        for row in grid[2:]:
            region = normalize_foreign_care_region(cell_text(row[0]) if row else None)
            if not region or "premier 400" in keytext(region):
                continue
            plans = {}
            for col_idx in range(1, len(row)):
                plan = column_plans.get(col_idx)
                kind = column_kinds.get(col_idx)
                if not plan or not kind:
                    continue
                pct = parse_percentage(cell_text(row[col_idx]))
                if pct is None:
                    continue
                plans.setdefault(plan, {})[kind] = pct
            if plans:
                matrix.append({"region": region, "plans": plans})

        note = None
        for footnote in table.get("footnotes") or []:
            cref = footnote.get("cref")
            text = find_doc_text(doc, cref) if cref else None
            if text and "premier 400" in keytext(text):
                note = text.lstrip("*").strip()
                break

        source_page = None
        for prov in table.get("prov") or []:
            page_no = prov.get("page_no")
            if isinstance(page_no, int):
                source_page = page_no
                break

        if matrix:
            return {"matrix": matrix, "note": note, "source_page": source_page}
    return None


def foreign_care_rule(region, first_pct, remaining_pct, plan_family):
    return {
        "criteria": f"Región: {region}",
        "raw_value": f"{format_percentage(first_pct)} / {format_percentage(remaining_pct)}",
        "amount": None,
        "secondary_amount": None,
        "currency": None,
        "percentage": first_pct,
        "secondary_percentage": remaining_pct,
        "unit": "%",
        "effective_start_date": None,
        "effective_end_date": None,
        "notes": f"{plan_family}: Primeros $100,000 pesos {format_percentage(first_pct)}; Resto del gasto {format_percentage(remaining_pct)}.",
    }


def rebuild_foreign_care_condition(data, run_dir):
    plan_family = resolve_premier_plan_family(data)
    if plan_family not in {"Premier 100", "Premier 200", "Premier 300"}:
        return 0

    docling_path = run_dir / "01_docling.json"
    if not docling_path.exists():
        return 0
    doc = json.loads(docling_path.read_text(encoding="utf-8"))
    parsed = extract_foreign_care_matrix_from_doc(doc)
    if not parsed:
        return 0

    target = None
    for cond in data.get("policy_conditions", []):
        if keytext(cond.get("condition_type")) == "cobertura de atencion en el extranjero":
            target = cond
            break
    if target is None:
        return 0

    rules = []
    for row in parsed["matrix"]:
        values = row["plans"].get(plan_family) or {}
        first_pct = values.get("first_100k_percentage")
        remaining_pct = values.get("remaining_expense_percentage")
        if first_pct is None or remaining_pct is None:
            continue
        rules.append(foreign_care_rule(row["region"], first_pct, remaining_pct, plan_family))

    note = parsed.get("note")
    if note:
        rules.append({
            "criteria": "Aplicabilidad de plan",
            "raw_value": note,
            "amount": None,
            "secondary_amount": None,
            "currency": None,
            "percentage": None,
            "secondary_percentage": None,
            "unit": None,
            "effective_start_date": None,
            "effective_end_date": None,
            "notes": note,
        })

    if not rules:
        return 0

    target["scope"] = "Aplica a Premier 100, Premier 200 y Premier 300; no aplica a Premier 400"
    target["description"] = (
        "La cobertura se determina por región y por plan. "
        f"Valores normalizados para el plan vigente {plan_family}: "
        "Primeros $100,000 pesos y resto del gasto."
    )
    target["rules"] = rules
    if parsed.get("source_page"):
        target["source_page"] = parsed["source_page"]
    return 1


def collapse_rebuilt_foreign_care_conditions(data):
    target = None
    kept = []
    collapsed = 0
    for cond in data.get("policy_conditions", []):
        if keytext(cond.get("condition_type")) != "cobertura de atencion en el extranjero":
            kept.append(cond)
            continue
        if target is None:
            target = cond
            kept.append(cond)
            continue
        merge_condition_provenance(target, cond)
        collapsed += 1
    if collapsed:
        data["policy_conditions"] = kept
    return collapsed


def extract_page1_metadata(text):
    t = norm(text)
    out = {}
    match = re.search(r"Fecha de Expedici[oó]n\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4})", t, re.I)
    if match:
        out["issue_date"] = iso_ymd(*match.groups())
    match = re.search(r"Vigencia de la P[oó]liza.*?Desde las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?Hasta las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?Duraci[oó]n\s+(\d+)\s+d[ií]as", t, re.I)
    if not match:
        match = re.search(r"Desde las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?Hasta las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?Duraci[oó]n\s+(\d+)\s+d[ií]as", t, re.I)
    if match:
        d1, m1, y1, d2, m2, y2, days = match.groups()
        out["coverage_start_date"] = iso_ymd(d1, m1, y1)
        out["coverage_end_date"] = iso_ymd(d2, m2, y2)
        out["term_days"] = int(days)
    if re.search(r"Conducto de pago\s+Intermediario", t, re.I):
        out["payment_channel"] = "Intermediario"
    match = re.search(r"Forma de pago\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)", t, re.I)
    if match:
        out["payment_method"] = match.group(1)
    if re.search(r"Moneda\s+Nacional", t, re.I):
        out["currency"] = "MXN"
    premium_block = t
    premium_match = re.search(r"Prima de la P[oó]liza(.*?)(?:Descripci[oó]n del Movimiento|Asegurado \(s\))", t, re.I)
    if premium_match:
        premium_block = premium_match.group(1)
    fields = {
        "net_premium": rf"Prima Neta\s+({money_pat()})",
        "installment_surcharge": rf"Fraccionado\s+({money_pat()})",
        "policy_fee": rf"Derecho de P[oó]liza\s+({money_pat()})",
        "tax_amount": rf"I\.V\.A\.\s*16%\s+({money_pat()})",
        "total_amount": rf"Importe Total a\s+Pagar\s+({money_pat()})",
    }
    for field, pattern in fields.items():
        match = re.search(pattern, premium_block, re.I)
        if match:
            out[field] = parse_money(match.group(1))
    match = re.search(r"I\.V\.A\.\s*(\d+(?:\.\d+)?)%", premium_block, re.I)
    if match:
        out["tax_rate_percent"] = float(match.group(1))
    match = re.search(r"Clave\s*:?\s*(\d{10})", t, re.I)
    if match:
        out["agent_code"] = match.group(1)
    return out


def add_warning(data, field, issue, severity="low", source_page=1):
    data.setdefault("validation", {}).setdefault("warnings", []).append({"field": field, "issue": issue, "severity": severity, "source_page": source_page})


def remove_stale_metadata_warnings(data):
    stale = {"coverage_start_date", "coverage_end_date", "term_days", "premium_summary", "premium_summary.payment_channel", "premium_summary.payment_method", "agent.agent_code", "issue_date", "issuance_date"}
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    kept = [warning for warning in warnings if warning.get("field") not in stale]
    removed = len(warnings) - len(kept)
    data["validation"]["warnings"] = kept
    return removed


def apply_metadata(data, parsed):
    policy = data.setdefault("policy", {})
    premium = data.setdefault("premium_summary", {})
    agent = data.setdefault("agent", {})
    for field in ("issue_date", "coverage_start_date", "coverage_end_date", "term_days", "currency"):
        if field in parsed:
            policy[field] = parsed[field]
    for field in ("net_premium", "installment_surcharge", "policy_fee", "tax_rate_percent", "tax_amount", "total_amount", "payment_method", "payment_channel"):
        if field in parsed:
            premium[field] = parsed[field]
    premium["source_page"] = 1
    if parsed.get("agent_code"):
        agent["agent_code"] = parsed["agent_code"]
    agent["source_page"] = 1


def validate_metadata(data):
    premium = data.get("premium_summary", {})
    required = ["net_premium", "installment_surcharge", "policy_fee", "tax_amount", "total_amount"]
    missing = [field for field in required if premium.get(field) is None]
    if missing:
        add_warning(data, "premium_summary", f"Missing deterministic premium field(s): {missing}", "high")
        return
    subtotal = float(premium["net_premium"]) + float(premium["installment_surcharge"]) + float(premium["policy_fee"])
    rate = float(premium.get("tax_rate_percent", 16))
    expected_tax = round(subtotal * rate / 100, 2)
    expected_total = round(subtotal + float(premium["tax_amount"]), 2)
    if abs(expected_tax - float(premium["tax_amount"])) > 0.25:
        add_warning(data, "premium_summary.tax_amount", f"Tax arithmetic mismatch: expected {expected_tax:.2f}, document {float(premium['tax_amount']):.2f}.", "medium")
    if abs(expected_total - float(premium["total_amount"])) > 0.02:
        add_warning(data, "premium_summary.total_amount", f"Total arithmetic mismatch: calculated {expected_total:.2f}, document {float(premium['total_amount']):.2f}.", "high")
    policy = data.get("policy", {})
    if policy.get("coverage_start_date") and policy.get("coverage_end_date") and policy.get("term_days") is not None:
        try:
            days = (date.fromisoformat(policy["coverage_end_date"]) - date.fromisoformat(policy["coverage_start_date"])).days
            if days != int(policy["term_days"]):
                add_warning(data, "term_days", f"Coverage dates imply {days} days but document states {policy['term_days']}.", "medium")
        except Exception:
            add_warning(data, "policy", "Invalid normalized ISO policy dates.", "high")


def process_gnp_policy(data: dict, pdf_path: Path, run_dir: Path) -> tuple[dict, dict]:
    pdf = pymupdf.open(pdf_path)
    reports = {}

    layout = detect_layout(data, pdf)
    insured_counts = {}
    insured_blocks = locate_insured_certificate_blocks(data, pdf)
    for insured, page_numbers in insured_blocks:
        rows = extract_certificate_coverages_from_block(pdf, page_numbers)
        insured["coverages"] = rows
        insured["premium"] = extract_insured_premium(pdf[page_numbers[0] - 1])
        insured_counts[insured.get("insured_number")] = len(rows)
    policy_rows = extract_policy_coverages(pdf[0])
    data["policy_coverages"] = policy_rows
    removed_coverage_warnings = clean_old_coverage_warnings(data)
    add_coverage_audit(data, layout, insured_counts, len(policy_rows))
    repaired_path = run_dir / "03_gnp_coverages.json"
    repaired_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    raw_count, final_count, moved, normalized_amounts, normalized_waiting = clean_policy_conditions(data)
    rebuilt_common_conditions = rebuild_repeated_certificate_conditions(data, pdf, insured_blocks)
    rebuilt_additional_conditions = rebuild_additional_certificate_conditions(data, pdf, insured_blocks)
    foreign_care_rebuilt = rebuild_foreign_care_condition(data, run_dir)
    foreign_care_collapsed = collapse_rebuilt_foreign_care_conditions(data) if foreign_care_rebuilt else 0
    sections_added, reg_found = extract_document_sections_and_regulatory(data, pdf)
    condition_heading_audit = build_condition_heading_audit(data, pdf, insured_blocks)
    dedupe_sections(data)
    add_condition_audit(data, raw_count, len(data.get("policy_conditions", [])), moved, sections_added, reg_found)
    cleaned_path = run_dir / "04_gnp_cleaned.json"
    cleaned_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    parsed = extract_page1_metadata(pdf[0].get_text("text"))
    removed_stale_warnings = remove_stale_metadata_warnings(data)
    apply_metadata(data, parsed)
    validate_metadata(data)
    add_warning(data, "metadata", "GNP metadata normalizer v2 applied deterministic page-1 normalization.", "low", 1)
    normalized_path = run_dir / "05_gnp_normalized.json"
    normalized_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    reports["layout"] = layout
    reports["insured_counts"] = insured_counts
    reports["policy_coverage_count"] = len(policy_rows)
    reports["removed_coverage_warnings"] = removed_coverage_warnings
    reports["raw_policy_conditions"] = raw_count
    reports["final_policy_conditions"] = len(data.get("policy_conditions", []))
    reports["moved_insured_conditions"] = moved
    reports["normalized_amount_conditions"] = normalized_amounts
    reports["normalized_waiting_conditions"] = normalized_waiting
    reports["rebuilt_common_conditions"] = rebuilt_common_conditions
    reports["rebuilt_additional_conditions"] = rebuilt_additional_conditions
    reports["condition_heading_pages"] = len(condition_heading_audit)
    reports["unmapped_condition_headings"] = sum(len(item["unmapped_headings"]) for item in condition_heading_audit)
    reports["foreign_care_rebuilt"] = foreign_care_rebuilt
    reports["foreign_care_collapsed"] = foreign_care_collapsed
    reports["final_insured_condition_counts"] = {
        insured.get("insured_number"): len(insured.get("conditions") or [])
        for insured in data.get("insureds", [])
    }
    reports["document_sections"] = len(data.get("document_sections", []))
    reports["regulatory_found"] = reg_found
    reports["removed_stale_metadata_warnings"] = removed_stale_warnings
    return data, reports
