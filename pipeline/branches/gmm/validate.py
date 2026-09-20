from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from pipeline.common.dates import normalize_dates_recursive
from pipeline.common.text import keytext, norm
from pipeline.common.validation import (
    add_warning,
    build_summary,
    dedupe_warnings,
    locate_regulatory_page,
    normalize_contact_fields,
    reconcile_currency_warnings,
    sanitize_strings_recursive,
    validate_dates,
    validate_duplicate_coverages,
    validate_insured_premium_reconciliation,
    validate_premiums,
    validate_provenance,
    validate_required_identifiers,
    validate_schema,
)

COMMON = "COMMON"
GMM_SPECIFIC = "GMM-SPECIFIC"

# Function reuse inventory. This branch file keeps only GMM-specific functions.
FUNCTION_CLASSIFICATION = {
    "has_premier_400_exclusion": GMM_SPECIFIC,
    "reconcile_condition_warnings": GMM_SPECIFIC,
    "fix_gnp_premier_foreign_care": GMM_SPECIFIC,
    "validate_agent_name": GMM_SPECIFIC,
    "validate_condition_heading_completeness": GMM_SPECIFIC,
    "validate_policy": GMM_SPECIFIC,
}


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
