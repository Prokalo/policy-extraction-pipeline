from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import jsonschema
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

REQUIRED_POLICY_FIELDS = ["insurer", "policy_number", "coverage_start_date", "coverage_end_date"]
REQUIRED_INSURED_FIELDS = ["insured_number", "name", "customer_code"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def deaccent(s):
    import unicodedata

    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def keytext(s):
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(s)).lower()).strip()


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


def sanitize_textual_artifacts(value):
    if not isinstance(value, str):
        return value
    cleaned = value
    cleaned = re.sub(r"(?i)</?think>", " ", cleaned)
    cleaned = re.sub(r"(?:(?<=\s)|^)/no_think\b", " ", cleaned, flags=re.I)
    if cleaned == value:
        return value
    return re.sub(r"\s+", " ", cleaned).strip()


def sanitize_strings_recursive(obj):
    changes = []
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if isinstance(value, str):
                new_value = sanitize_textual_artifacts(value)
                if new_value != value:
                    obj[key] = new_value
                    changes.append((key, value, new_value))
            else:
                changes.extend(sanitize_strings_recursive(value))
    elif isinstance(obj, list):
        for item in obj:
            changes.extend(sanitize_strings_recursive(item))
    return changes


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


def add_warning(data, field, issue, severity="low", source_page=None):
    data.setdefault("validation", {}).setdefault("warnings", []).append({"field": field, "issue": issue, "severity": severity, "source_page": source_page})


def sanitize_labeled_placeholder(value, labels):
    text = norm(value)
    if not text:
        return None
    if any(re.fullmatch(rf"{label}\s*:?", text, re.I) for label in labels):
        return None
    return value


def normalize_contact_fields(data):
    changes = []
    field_labels = {
        "email": [r"correo electr[oó]nico", r"correo", r"email", r"e-mail"],
        "phone": [r"tel[eé]fono", r"telefono", r"tel"],
        "tax_id": [r"r\.?f\.?c\.?", r"rfc"],
    }
    candidates = [("policyholder", data.get("policyholder") or {})]
    for idx, insured in enumerate(data.get("insureds") or []):
        candidates.append((f"insureds[{idx}]", insured))
    for prefix, obj in candidates:
        for field, labels in field_labels.items():
            if field not in obj:
                continue
            value = obj.get(field)
            if value is None:
                continue
            new_value = sanitize_labeled_placeholder(value, labels)
            if field == "email" and new_value is not None and not EMAIL_RE.fullmatch(norm(str(new_value))):
                new_value = None
            if new_value != value:
                obj[field] = new_value
                changes.append((f"{prefix}.{field}", value, new_value))
    return changes


def has_premier_400_exclusion(cond):
    values = [cond.get("scope"), cond.get("description")]
    for rule in cond.get("rules", []):
        values.extend([rule.get("criteria"), rule.get("raw_value"), rule.get("notes")])
    return any("premier 400" in keytext(value) for value in values if value)


def reconcile_condition_warnings(data):
    has_foreign_care_exclusion = any(
        keytext(cond.get("condition_type")) == "cobertura de atencion en el extranjero" and has_premier_400_exclusion(cond)
        for cond in data.get("policy_conditions", [])
    )
    if not has_foreign_care_exclusion:
        return 0

    kept = []
    removed = 0
    for warning in data.setdefault("validation", {}).setdefault("warnings", []):
        issue = keytext(warning.get("issue"))
        field = keytext(warning.get("field"))
        stale_foreign_care_warning = (
            "premier 400" in issue
            and ("not included" in issue or "no se incluye" in issue)
            and ("condition" in field or "policy conditions" in field or field == "")
        )
        if stale_foreign_care_warning:
            removed += 1
            continue
        kept.append(warning)
    data["validation"]["warnings"] = kept
    return removed


def has_explicit_usd_coverage_values(data):
    coverage_groups = [data.get("policy_coverages") or []]
    coverage_groups.extend((insured.get("coverages") or []) for insured in (data.get("insureds") or []))
    for rows in coverage_groups:
        for row in rows:
            for field in ("sum_insured", "deductible", "service_cost"):
                if (row.get(field) or {}).get("currency") == "USD":
                    return True
    return False


def reconcile_currency_warnings(data):
    if keytext((data.get("policy") or {}).get("currency")) != "mxn" or not has_explicit_usd_coverage_values(data):
        return 0
    kept = []
    removed = 0
    for warning in data.setdefault("validation", {}).setdefault("warnings", []):
        issue = keytext(warning.get("issue"))
        if "currency is normalized to mxn" in issue and "usd" in issue:
            removed += 1
            continue
        kept.append(warning)
    data["validation"]["warnings"] = kept
    return removed


def dedupe_warnings(data):
    seen = set()
    out = []
    for warning in data.setdefault("validation", {}).setdefault("warnings", []):
        sig = (warning.get("field"), warning.get("issue"), warning.get("severity"), warning.get("source_page"))
        if sig not in seen:
            seen.add(sig)
            out.append(warning)
    data["validation"]["warnings"] = out


def fix_gnp_premier_foreign_care(data):
    plan = keytext(data.get("policy", {}).get("plan_name") or data.get("policy", {}).get("plan_raw_text"))
    changes = 0
    for cond in data.get("policy_conditions", []):
        if keytext(cond.get("condition_type")) != "cobertura de atencion en el extranjero":
            continue
        desc = keytext(cond.get("description"))
        if "no aplica para premier 400" in desc:
            if any(item in plan for item in ("premier 100", "premier 200", "premier 300")):
                new_scope = "Aplica a Premier 100, Premier 200 y Premier 300; no aplica a Premier 400"
                if cond.get("scope") != new_scope:
                    cond["scope"] = new_scope
                    changes += 1
            for rule in cond.get("rules", []):
                if "no aplica para premier 400" in keytext(rule.get("raw_value")) and keytext(rule.get("criteria")) == "cobertura de preexistencia":
                    rule["criteria"] = "Aplicabilidad de plan"
                    changes += 1
    return changes


def locate_regulatory_page(data, pdf):
    reg = data.get("regulatory") or {}
    number = norm(reg.get("registration_number"))
    if not number:
        return False
    variants = {number, number.replace("-", "−"), number.replace("−", "-")}
    for i, page in enumerate(pdf, start=1):
        normalized = page.get_text("text").replace("−", "-")
        if any(item.replace("−", "-") in normalized for item in variants):
            if reg.get("source_page") != i:
                reg["source_page"] = i
                data["regulatory"] = reg
                return True
            return False
    add_warning(data, "regulatory.registration_number", f"Registration number {number} was not found in the source PDF.", "medium", reg.get("source_page"))
    return False


def validate_required_identifiers(data):
    policy = data.get("policy") or {}
    for field in REQUIRED_POLICY_FIELDS:
        if policy.get(field) in (None, ""):
            add_warning(data, f"policy.{field}", "Required policy field is missing.", "high", policy.get("source_page"))
    agent = data.get("agent") or {}
    if not agent.get("agent_code"):
        add_warning(data, "agent.agent_code", "Agent code is missing.", "medium", agent.get("source_page"))
    insureds = data.get("insureds") or []
    if not insureds:
        add_warning(data, "insureds", "No insured records were extracted.", "high", None)
    for idx, insured in enumerate(insureds, start=1):
        for field in REQUIRED_INSURED_FIELDS:
            if insured.get(field) in (None, ""):
                add_warning(data, f"insureds[{idx - 1}].{field}", "Required insured field is missing.", "high", insured.get("source_page"))


def validate_premiums(data):
    premium = data.get("premium_summary") or {}
    fields = ["net_premium", "installment_surcharge", "policy_fee", "tax_amount", "total_amount"]
    if any(premium.get(field) is None for field in fields):
        missing = [field for field in fields if premium.get(field) is None]
        add_warning(data, "premium_summary", f"Cannot fully validate premium arithmetic; missing {missing}.", "high", premium.get("source_page"))
        return
    subtotal = float(premium["net_premium"]) + float(premium["installment_surcharge"]) + float(premium["policy_fee"])
    rate = float(premium.get("tax_rate_percent") or 0)
    expected_tax = round(subtotal * rate / 100, 2)
    expected_total = round(subtotal + float(premium["tax_amount"]), 2)
    # Printed policy tax can reflect per-insured rounding or unprinted fractional
    # premiums. Preserve the explicit document amount and flag only materially
    # larger differences as high severity.
    tax_delta = abs(expected_tax - float(premium["tax_amount"]))
    if tax_delta > 0.02:
        severity = "high" if tax_delta > 0.25 else "medium"
        add_warning(data, "premium_summary.tax_amount", f"Tax mismatch: expected {expected_tax:.2f}, document has {float(premium['tax_amount']):.2f}.", severity, premium.get("source_page"))
    if abs(expected_total - float(premium["total_amount"])) > 0.02:
        add_warning(data, "premium_summary.total_amount", f"Total mismatch: expected {expected_total:.2f}, document has {float(premium['total_amount']):.2f}.", "high", premium.get("source_page"))


def validate_insured_premium_reconciliation(data, tolerance=0.05):
    insureds = data.get("insureds") or []
    if not insureds:
        return
    policy = data.get("premium_summary") or {}
    for field in ("net_premium", "installment_surcharge", "policy_fee", "tax_amount", "total_amount"):
        policy_value = policy.get(field)
        if policy_value is None:
            continue
        insured_values = [insured.get("premium", {}).get(field) for insured in insureds]
        if any(value is None for value in insured_values):
            add_warning(data, f"premium_summary.{field}", f"Cannot fully reconcile {field}; at least one insured premium is missing.", "medium", policy.get("source_page"))
            continue
        total = round(sum(float(value) for value in insured_values), 2)
        if abs(total - float(policy_value)) > tolerance:
            add_warning(data, f"premium_summary.{field}", f"Sum of insured {field} values ({total:.2f}) does not match policy total ({float(policy_value):.2f}).", "high", policy.get("source_page"))


def iter_source_pages(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            next_path = f"{path}.{key}" if path else key
            if key == "source_page":
                yield next_path, value
            else:
                yield from iter_source_pages(value, next_path)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from iter_source_pages(item, f"{path}[{idx}]")


def validate_provenance(data, pdf_page_count):
    for path, page in iter_source_pages(data):
        if page is None:
            continue
        if not isinstance(page, int) or not (1 <= page <= pdf_page_count):
            add_warning(data, path, f"Invalid source_page={page}; PDF has {pdf_page_count} pages.", "high", None)


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
    seen = set()
    for row in data.get("policy_coverages") or []:
        sig = coverage_signature(row)
        if sig in seen:
            add_warning(data, "policy_coverages", f"Duplicate policy coverage detected: {row.get('name')}.", "medium", row.get("source_page"))
        seen.add(sig)
    for insured in data.get("insureds") or []:
        seen = set()
        for row in insured.get("coverages") or []:
            sig = coverage_signature(row)
            if sig in seen:
                add_warning(data, f"insureds[{insured.get('insured_number')}].coverages", f"Duplicate insured coverage detected: {row.get('name')}.", "medium", row.get("source_page"))
            seen.add(sig)


def validate_dates(data):
    policy = data.get("policy") or {}
    for field in ("issue_date", "coverage_start_date", "coverage_end_date"):
        value = policy.get(field)
        if value:
            try:
                date.fromisoformat(value)
            except Exception:
                add_warning(data, f"policy.{field}", f"Date is not valid ISO YYYY-MM-DD: {value}", "high", policy.get("source_page"))
    for insured in data.get("insureds") or []:
        for field in ("birth_date", "seniority_date"):
            value = insured.get(field)
            if value:
                try:
                    date.fromisoformat(value)
                except Exception:
                    add_warning(data, f"insureds[{insured.get('insured_number')}].{field}", f"Date is not valid ISO YYYY-MM-DD: {value}", "high", insured.get("source_page"))


def validate_agent_name(data):
    name = norm((data.get("agent") or {}).get("name"))
    if name and re.search(r"\bS\.A\.\s+DE\s+C\.$", name, re.I):
        add_warning(data, "agent.name", "Agent name may be visually truncated in the source text; agent_code should be treated as the authoritative identifier.", "low", (data.get("agent") or {}).get("source_page"))


def validate_condition_heading_completeness(data):
    unmapped_count = 0
    for page in data.get("condition_heading_audit") or []:
        for heading in page.get("unmapped_headings") or []:
            unmapped_count += 1
            add_warning(
                data,
                "condition_heading_audit",
                f'Unmapped meaningful condition heading for insured {page.get("insured_number")}: "{heading}".',
                "high",
                page.get("page"),
            )
    return unmapped_count


def validate_schema(data, schema_path: Path):
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.absolute_path))
    for error in errors:
        path = ".".join(str(item) for item in error.absolute_path) or "$"
        add_warning(data, path, f"Schema validation error: {error.message}", "high", None)
    return errors


def build_summary(data):
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    counts = {"low": 0, "medium": 0, "high": 0}
    for warning in warnings:
        severity = warning.get("severity", "low")
        counts[severity] = counts.get(severity, 0) + 1
    sql_ready = counts.get("high", 0) == 0
    data["validation"]["summary"] = {
        "high_severity_count": counts.get("high", 0),
        "medium_severity_count": counts.get("medium", 0),
        "low_severity_count": counts.get("low", 0),
        "sql_ready": sql_ready,
    }
    return data["validation"]["summary"]


def validate_policy(data: dict, pdf_path: Path, schema_path: Path) -> tuple[dict, dict]:
    pdf = pymupdf.open(pdf_path)
    string_sanitizations = sanitize_strings_recursive(data)
    contact_normalizations = normalize_contact_fields(data)
    date_changes = normalize_dates_recursive(data)
    semantic_changes = fix_gnp_premier_foreign_care(data)
    reconciled_warnings = reconcile_condition_warnings(data)
    reconciled_currency_warnings = reconcile_currency_warnings(data)
    reg_changed = locate_regulatory_page(data, pdf)
    validate_required_identifiers(data)
    validate_premiums(data)
    validate_insured_premium_reconciliation(data)
    validate_provenance(data, len(pdf))
    validate_duplicate_coverages(data)
    validate_dates(data)
    validate_agent_name(data)
    unmapped_condition_headings = validate_condition_heading_completeness(data)
    schema_errors = validate_schema(data, schema_path)
    add_warning(data, "validation", f"Final validator applied: {len(string_sanitizations)} string sanitization(s), {len(contact_normalizations)} contact normalization(s), {len(date_changes)} date normalization(s), {semantic_changes} semantic correction(s), {reconciled_warnings} reconciled condition warning(s), {reconciled_currency_warnings} reconciled currency warning(s), regulatory provenance corrected={reg_changed}, schema errors={len(schema_errors)}.", "low", None)
    dedupe_warnings(data)
    summary = build_summary(data)
    report = {
        "string_sanitizations": len(string_sanitizations),
        "contact_normalizations": len(contact_normalizations),
        "date_normalizations": len(date_changes),
        "semantic_corrections": semantic_changes,
        "reconciled_condition_warnings": reconciled_warnings,
        "reconciled_currency_warnings": reconciled_currency_warnings,
        "regulatory_provenance_corrected": reg_changed,
        "schema_error_count": len(schema_errors),
        "unmapped_condition_heading_count": unmapped_condition_headings,
        "summary": summary,
    }
    return data, report
