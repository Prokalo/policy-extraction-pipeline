#!/usr/bin/env python3
"""
Final insurance JSON validator / normalizer.

Designed for the current GNP Docling pipeline, but most validations are
insurer-agnostic.

Usage:
    python3 final_validate.py insurance_v3_final.json poliza.pdf

Output:
    insurance_v3_validated.json

What it does:
- normalizes all known dates to ISO YYYY-MM-DD
- fixes the tested GNP Premier foreign-care applicability contamination
- corrects regulatory provenance by locating the registration in the PDF
- validates required identifiers
- validates premium arithmetic
- validates provenance page numbers
- checks duplicate coverage rows
- produces validation.summary.sql_ready
"""

import argparse
import copy
import json
import re
from datetime import datetime, date
from pathlib import Path

import pymupdf


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

REQUIRED_POLICY_FIELDS = [
    "insurer",
    "policy_number",
    "coverage_start_date",
    "coverage_end_date",
]

REQUIRED_INSURED_FIELDS = [
    "insured_number",
    "name",
    "customer_code",
]


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def deaccent(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def keytext(s):
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(s)).lower()).strip()


def parse_date(value):
    """
    Normalize common source formats to YYYY-MM-DD.
    Return original value if it cannot be safely parsed.
    """
    if value is None:
        return None

    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")

    s = norm(str(value))
    if not s:
        return None

    # Already ISO.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            date.fromisoformat(s)
            return s
        except ValueError:
            return value

    # dd/mm/yyyy, dd-mm-yyyy, dd mm yyyy
    m = re.fullmatch(r"(\d{1,2})[\/\-\s](\d{1,2})[\/\-\s](\d{4})", s)
    if m:
        d, mth, y = map(int, m.groups())
        try:
            return date(y, mth, d).isoformat()
        except ValueError:
            return value

    return value


def normalize_dates_recursive(obj):
    changes = []

    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in DATE_FIELDS and v is not None:
                nv = parse_date(v)
                if nv != v:
                    obj[k] = nv
                    changes.append((k, v, nv))
            else:
                changes.extend(normalize_dates_recursive(v))

    elif isinstance(obj, list):
        for item in obj:
            changes.extend(normalize_dates_recursive(item))

    return changes


def add_warning(data, field, issue, severity="low", source_page=None):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": field,
        "issue": issue,
        "severity": severity,
        "source_page": source_page,
    })


def dedupe_warnings(data):
    seen = set()
    out = []
    for w in data.setdefault("validation", {}).setdefault("warnings", []):
        sig = (
            w.get("field"),
            w.get("issue"),
            w.get("severity"),
            w.get("source_page"),
        )
        if sig not in seen:
            seen.add(sig)
            out.append(w)
    data["validation"]["warnings"] = out


def fix_gnp_premier_foreign_care(data):
    """
    Fix the known semantic contamination from the Qwen extraction:
    "* Esta cobertura no aplica para Premier 400" was attached to a
    preexistence rule, while it belongs to foreign-care applicability.
    """
    plan = keytext(
        data.get("policy", {}).get("plan_name")
        or data.get("policy", {}).get("plan_raw_text")
    )

    changes = 0

    for cond in data.get("policy_conditions", []):
        if keytext(cond.get("condition_type")) != "cobertura de atencion en el extranjero":
            continue

        desc = keytext(cond.get("description"))

        if "no aplica para premier 400" in desc:
            if any(x in plan for x in ("premier 100", "premier 200", "premier 300")):
                new_scope = "Aplica a Premier 100, Premier 200 y Premier 300; no aplica a Premier 400"
                if cond.get("scope") != new_scope:
                    cond["scope"] = new_scope
                    changes += 1

            for rule in cond.get("rules", []):
                if "no aplica para premier 400" in keytext(rule.get("raw_value")):
                    if keytext(rule.get("criteria")) == "cobertura de preexistencia":
                        rule["criteria"] = "Aplicabilidad de plan"
                        changes += 1

    return changes


def locate_regulatory_page(data, pdf):
    reg = data.get("regulatory") or {}
    number = norm(reg.get("registration_number"))
    if not number:
        return False

    # Normalize different dash glyphs.
    variants = {
        number,
        number.replace("-", "−"),
        number.replace("−", "-"),
    }

    for i, page in enumerate(pdf, start=1):
        text = page.get_text("text")
        normalized_text = text.replace("−", "-")
        if any(v.replace("−", "-") in normalized_text for v in variants):
            if reg.get("source_page") != i:
                reg["source_page"] = i
                data["regulatory"] = reg
                return True
            return False

    add_warning(
        data,
        "regulatory.registration_number",
        f"Registration number {number} was not found in the source PDF.",
        severity="medium",
        source_page=reg.get("source_page"),
    )
    return False


def validate_required_identifiers(data):
    policy = data.get("policy") or {}

    for field in REQUIRED_POLICY_FIELDS:
        if policy.get(field) in (None, ""):
            add_warning(
                data,
                f"policy.{field}",
                "Required policy field is missing.",
                severity="high",
                source_page=policy.get("source_page"),
            )

    agent = data.get("agent") or {}
    if not agent.get("agent_code"):
        add_warning(
            data,
            "agent.agent_code",
            "Agent code is missing.",
            severity="medium",
            source_page=agent.get("source_page"),
        )

    insureds = data.get("insureds") or []
    if not insureds:
        add_warning(
            data,
            "insureds",
            "No insured records were extracted.",
            severity="high",
            source_page=None,
        )

    for idx, insured in enumerate(insureds, start=1):
        for field in REQUIRED_INSURED_FIELDS:
            if insured.get(field) in (None, ""):
                add_warning(
                    data,
                    f"insureds[{idx-1}].{field}",
                    "Required insured field is missing.",
                    severity="high",
                    source_page=insured.get("source_page"),
                )


def validate_premiums(data):
    p = data.get("premium_summary") or {}

    fields = [
        "net_premium",
        "installment_surcharge",
        "policy_fee",
        "tax_amount",
        "total_amount",
    ]

    if any(p.get(f) is None for f in fields):
        missing = [f for f in fields if p.get(f) is None]
        add_warning(
            data,
            "premium_summary",
            f"Cannot fully validate premium arithmetic; missing {missing}.",
            severity="high",
            source_page=p.get("source_page"),
        )
        return

    subtotal = (
        float(p["net_premium"])
        + float(p["installment_surcharge"])
        + float(p["policy_fee"])
    )

    rate = float(p.get("tax_rate_percent") or 0)
    expected_tax = round(subtotal * rate / 100, 2)
    expected_total = round(subtotal + float(p["tax_amount"]), 2)

    if abs(expected_tax - float(p["tax_amount"])) > 0.02:
        add_warning(
            data,
            "premium_summary.tax_amount",
            f"Tax mismatch: expected {expected_tax:.2f}, document has {float(p['tax_amount']):.2f}.",
            severity="high",
            source_page=p.get("source_page"),
        )

    if abs(expected_total - float(p["total_amount"])) > 0.02:
        add_warning(
            data,
            "premium_summary.total_amount",
            f"Total mismatch: expected {expected_total:.2f}, document has {float(p['total_amount']):.2f}.",
            severity="high",
            source_page=p.get("source_page"),
        )


def iter_source_pages(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k == "source_page":
                yield p, v
            else:
                yield from iter_source_pages(v, p)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from iter_source_pages(item, f"{path}[{i}]")


def validate_provenance(data, pdf_page_count):
    for path, page in iter_source_pages(data):
        if page is None:
            continue
        if not isinstance(page, int) or not (1 <= page <= pdf_page_count):
            add_warning(
                data,
                path,
                f"Invalid source_page={page}; PDF has {pdf_page_count} pages.",
                severity="high",
                source_page=None,
            )


def coverage_signature(row):
    def raw(obj):
        return norm((obj or {}).get("raw_value"))

    return (
        keytext(row.get("name")),
        keytext(row.get("scope")),
        raw(row.get("sum_insured")),
        raw(row.get("deductible")),
        raw(row.get("coinsurance")),
        raw(row.get("service_cost")),
        keytext(row.get("status")),
    )


def validate_duplicate_coverages(data):
    # Policy-level duplicates.
    seen = set()
    for row in data.get("policy_coverages") or []:
        sig = coverage_signature(row)
        if sig in seen:
            add_warning(
                data,
                "policy_coverages",
                f"Duplicate policy coverage detected: {row.get('name')}.",
                severity="medium",
                source_page=row.get("source_page"),
            )
        seen.add(sig)

    # Insured-level duplicates.
    for insured in data.get("insureds") or []:
        seen = set()
        for row in insured.get("coverages") or []:
            sig = coverage_signature(row)
            if sig in seen:
                add_warning(
                    data,
                    f"insureds[{insured.get('insured_number')}].coverages",
                    f"Duplicate insured coverage detected: {row.get('name')}.",
                    severity="medium",
                    source_page=row.get("source_page"),
                )
            seen.add(sig)


def validate_dates(data):
    # Policy dates must be valid ISO by this stage.
    policy = data.get("policy") or {}

    for field in ("issue_date", "coverage_start_date", "coverage_end_date"):
        value = policy.get(field)
        if value:
            try:
                date.fromisoformat(value)
            except Exception:
                add_warning(
                    data,
                    f"policy.{field}",
                    f"Date is not valid ISO YYYY-MM-DD: {value}",
                    severity="high",
                    source_page=policy.get("source_page"),
                )

    for insured in data.get("insureds") or []:
        for field in ("birth_date", "seniority_date"):
            value = insured.get(field)
            if value:
                try:
                    date.fromisoformat(value)
                except Exception:
                    add_warning(
                        data,
                        f"insureds[{insured.get('insured_number')}].{field}",
                        f"Date is not valid ISO YYYY-MM-DD: {value}",
                        severity="high",
                        source_page=insured.get("source_page"),
                    )


def validate_agent_name(data):
    name = norm((data.get("agent") or {}).get("name"))
    if name and re.search(r"\bS\.A\.\s+DE\s+C\.$", name, re.I):
        add_warning(
            data,
            "agent.name",
            "Agent name may be visually truncated in the source text; agent_code should be treated as the authoritative identifier.",
            severity="low",
            source_page=(data.get("agent") or {}).get("source_page"),
        )


def build_summary(data):
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    counts = {"low": 0, "medium": 0, "high": 0}

    for w in warnings:
        sev = w.get("severity", "low")
        counts[sev] = counts.get(sev, 0) + 1

    sql_ready = counts.get("high", 0) == 0

    data["validation"]["summary"] = {
        "high_severity_count": counts.get("high", 0),
        "medium_severity_count": counts.get("medium", 0),
        "low_severity_count": counts.get("low", 0),
        "sql_ready": sql_ready,
    }

    return data["validation"]["summary"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("pdf")
    ap.add_argument("--output", default="insurance_v3_validated.json")
    args = ap.parse_args()

    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    pdf = pymupdf.open(args.pdf)

    # Preserve prior audit warnings; add validator audit at end.
    date_changes = normalize_dates_recursive(data)
    semantic_changes = fix_gnp_premier_foreign_care(data)
    reg_changed = locate_regulatory_page(data, pdf)

    validate_required_identifiers(data)
    validate_premiums(data)
    validate_provenance(data, len(pdf))
    validate_duplicate_coverages(data)
    validate_dates(data)
    validate_agent_name(data)

    add_warning(
        data,
        "validation",
        (
            f"Final validator applied: {len(date_changes)} date normalization(s), "
            f"{semantic_changes} semantic correction(s), "
            f"regulatory provenance corrected={reg_changed}."
        ),
        severity="low",
        source_page=None,
    )

    dedupe_warnings(data)
    summary = build_summary(data)

    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("FINAL VALIDATION")
    print(f"Date normalizations: {len(date_changes)}")
    print(f"Semantic corrections: {semantic_changes}")
    print(f"Regulatory provenance corrected: {reg_changed}")
    print(f"High severity warnings: {summary['high_severity_count']}")
    print(f"Medium severity warnings: {summary['medium_severity_count']}")
    print(f"Low severity warnings: {summary['low_severity_count']}")
    print(f"SQL ready: {summary['sql_ready']}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
