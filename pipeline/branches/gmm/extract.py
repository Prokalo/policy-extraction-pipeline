from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from docling.document_converter import DocumentConverter
from jsonschema import Draft202012Validator
from ollama import chat

from pipeline.config import PipelineConfig


NOTES_MAX_LENGTH = 300
COMMON = "COMMON"
GMM_SPECIFIC = "GMM-SPECIFIC"

# Function reuse inventory. COMMON functions are candidates for shared branch
# utilities; GMM-SPECIFIC functions encode the current Gastos Médicos flow.
FUNCTION_CLASSIFICATION = {
    "load_json": COMMON,
    "convert_pdf_to_docling_dict": COMMON,
    "save_json": COMMON,
    "norm": COMMON,
    "deaccent": COMMON,
    "keytext": COMMON,
    "warning_schema": COMMON,
    "money_schema": COMMON,
    "condition_schema": GMM_SPECIFIC,
    "coverage_schema": GMM_SPECIFIC,
    "schema_policy_meta": GMM_SPECIFIC,
    "schema_insured": GMM_SPECIFIC,
    "schema_conditions": GMM_SPECIFIC,
    "schema_condition_section": GMM_SPECIFIC,
    "_scalar_strings": COMMON,
    "validate_notes": COMMON,
    "validate_structured_response": COMMON,
    "call_structured": COMMON,
    "build_page_evidence": COMMON,
    "page_sort_y": COMMON,
    "build_page_items": COMMON,
    "clean_evidence_text": COMMON,
    "format_page_item": COMMON,
    "render_pages": COMMON,
    "flat_page_text": COMMON,
    "page_text_from_items": COMMON,
    "canonical_condition_heading": GMM_SPECIFIC,
    "render_page_items": COMMON,
    "split_condition_sections": GMM_SPECIFIC,
    "slugify": COMMON,
    "condition_section_is_empty": COMMON,
    "failure_warning_for_section": COMMON,
    "extract_condition_sections": GMM_SPECIFIC,
    "certificate_heading_page": GMM_SPECIFIC,
    "detect_insured_anchor": GMM_SPECIFIC,
    "discover_insured_certificate_blocks": GMM_SPECIFIC,
    "empty_final": GMM_SPECIFIC,
    "extract_policy_from_docling": GMM_SPECIFIC,
}


SYSTEM_PROMPT = """You are a strict Spanish insurance-policy extraction engine.
Extract only facts explicitly supported by the supplied Docling evidence. Never invent; if uncertain return null.
Preserve names, RFC/tax IDs, policy numbers, customer codes, Spanish plan names and coverage names exactly.
Normalize pesos -> MXN and dls/dólares norteamericanos -> USD only in normalized currency fields.
Never borrow sum insured, deductible, coinsurance, or service cost from an adjacent row. Values must belong to the same logical coverage row.
If row association is uncertain, return null and add a warning. Preserve every row of rule tables.
The notes field is only for meaningful facts not represented by another structured field. Never put a structured value in notes a second time.
Never copy tables, Markdown table syntax, cell separators, empty cells, page formatting, or repeated whitespace into notes.
Use null when there is no additional fact for notes, and never exceed 300 characters. Return JSON matching the supplied schema."""

CONDITION_CHROME = {
    "poliza de seguro gastos medicos",
    "certificado de cobertura por asegurado",
    "condiciones especiales",
    "nacional",
    "monto",
    "monto maximo a pagar",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def convert_pdf_to_docling_dict(pdf_path: Path) -> dict:
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    return json.loads(result.document.model_dump_json())


def save_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def deaccent(text: str | None) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def keytext(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(text)).lower()).strip()


def warning_schema():
    return {"type": "object", "properties": {
        "field": {"type": ["string", "null"]}, "issue": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
        "source_page": {"type": ["integer", "null"]}},
        "required": ["field", "issue", "severity", "source_page"]}


def money_schema():
    return {"type": "object", "properties": {
        "raw_value": {"type": ["string", "null"]}, "amount": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]}},
        "required": ["raw_value", "amount", "currency"]}


def condition_schema():
    return {"type": "object", "properties": {
        "condition_type": {"type": ["string", "null"]}, "scope": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "criteria": {"type": ["string", "null"]}, "raw_value": {"type": ["string", "null"]},
            "amount": {"type": ["number", "null"]}, "secondary_amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]}, "percentage": {"type": ["number", "null"]},
            "secondary_percentage": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"]},
            "effective_start_date": {"type": ["string", "null"]}, "effective_end_date": {"type": ["string", "null"]},
            "notes": {"type": ["string", "null"], "maxLength": NOTES_MAX_LENGTH}},
            "required": ["criteria", "raw_value", "amount", "secondary_amount", "currency", "percentage", "secondary_percentage", "unit", "effective_start_date", "effective_end_date", "notes"]}},
        "source_page": {"type": ["integer", "null"]}},
        "required": ["condition_type", "scope", "description", "rules", "source_page"]}


def coverage_schema():
    return {"type": "object", "properties": {
        "category": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]},
        "scope": {"type": ["string", "null"]}, "status": {"type": ["string", "null"]},
        "sum_insured": money_schema(), "deductible": money_schema(),
        "coinsurance": {"type": "object", "properties": {
            "raw_value": {"type": ["string", "null"]}, "percentage": {"type": ["number", "null"]},
            "applies": {"type": ["boolean", "null"]}}, "required": ["raw_value", "percentage", "applies"]},
        "service_cost": {"type": "object", "properties": {
            "raw_value": {"type": ["string", "null"]}, "amount": {"type": ["number", "null"]},
            "currency": {"type": ["string", "null"]}, "unit": {"type": ["string", "null"]}},
            "required": ["raw_value", "amount", "currency", "unit"]},
        "notes": {"type": ["string", "null"], "maxLength": NOTES_MAX_LENGTH}, "source_page": {"type": ["integer", "null"]}},
        "required": ["category", "name", "scope", "status", "sum_insured", "deductible", "coinsurance", "service_cost", "notes", "source_page"]}


def schema_policy_meta():
    return {"type": "object", "properties": {
        "document": {"type": "object", "properties": {
            "document_type": {"type": ["string", "null"]}, "language": {"type": ["string", "null"]}, "page_count": {"type": ["integer", "null"]}},
            "required": ["document_type", "language", "page_count"]},
        "policy": {"type": "object", "properties": {
            "insurer": {"type": ["string", "null"]}, "policy_number": {"type": ["string", "null"]}, "version": {"type": ["string", "null"]},
            "renewal_number": {"type": ["string", "null"]}, "product_line": {"type": ["string", "null"]}, "plan_raw_text": {"type": ["string", "null"]},
            "plan_name": {"type": ["string", "null"]}, "hospital_level": {"type": ["string", "null"]}, "scheme_name": {"type": ["string", "null"]},
            "issue_date": {"type": ["string", "null"]}, "coverage_start_date": {"type": ["string", "null"]}, "coverage_end_date": {"type": ["string", "null"]},
            "term_days": {"type": ["integer", "null"]}, "currency": {"type": ["string", "null"]}, "movement_type": {"type": ["string", "null"]},
            "movement_description": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
            "required": ["insurer", "policy_number", "version", "renewal_number", "product_line", "plan_raw_text", "plan_name", "hospital_level", "scheme_name", "issue_date", "coverage_start_date", "coverage_end_date", "term_days", "currency", "movement_type", "movement_description", "source_page"]},
        "policyholder": {"type": "object", "properties": {
            "name": {"type": ["string", "null"]}, "customer_code": {"type": ["string", "null"]}, "tax_id": {"type": ["string", "null"]},
            "address": {"type": ["string", "null"]}, "email": {"type": ["string", "null"]}, "phone": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
            "required": ["name", "customer_code", "tax_id", "address", "email", "phone", "source_page"]},
        "premium_summary": {"type": "object", "properties": {
            "net_premium": {"type": ["number", "null"]}, "installment_surcharge": {"type": ["number", "null"]}, "policy_fee": {"type": ["number", "null"]},
            "tax_rate_percent": {"type": ["number", "null"]}, "tax_amount": {"type": ["number", "null"]}, "total_amount": {"type": ["number", "null"]},
            "payment_method": {"type": ["string", "null"]}, "payment_channel": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
            "required": ["net_premium", "installment_surcharge", "policy_fee", "tax_rate_percent", "tax_amount", "total_amount", "payment_method", "payment_channel", "source_page"]},
        "agent": {"type": "object", "properties": {
            "name": {"type": ["string", "null"]}, "agent_code": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
            "required": ["name", "agent_code", "source_page"]},
        "regulatory": {"type": "object", "properties": {
            "registration_number": {"type": ["string", "null"]}, "registration_date": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
            "required": ["registration_number", "registration_date", "source_page"]},
        "warnings": {"type": "array", "items": warning_schema()}},
        "required": ["document", "policy", "policyholder", "premium_summary", "agent", "regulatory", "warnings"]}


def schema_insured():
    return {"type": "object", "properties": {
        "insured_number": {"type": ["integer", "null"]}, "role": {"type": ["string", "null"]}, "name": {"type": ["string", "null"]},
        "customer_code": {"type": ["string", "null"]}, "birth_date": {"type": ["string", "null"]}, "gender": {"type": ["string", "null"]},
        "seniority_date": {"type": ["string", "null"]}, "coverage_scope": {"type": ["string", "null"]},
        "premium": {"type": "object", "properties": {
            "net_premium": {"type": ["number", "null"]}, "installment_surcharge": {"type": ["number", "null"]}, "policy_fee": {"type": ["number", "null"]},
            "tax_amount": {"type": ["number", "null"]}, "total_amount": {"type": ["number", "null"]}},
            "required": ["net_premium", "installment_surcharge", "policy_fee", "tax_amount", "total_amount"]},
        "coverages": {"type": "array", "items": coverage_schema()},
        "conditions": {"type": "array", "items": condition_schema()},
        "source_page": {"type": ["integer", "null"]}, "warnings": {"type": "array", "items": warning_schema()}},
        "required": ["insured_number", "role", "name", "customer_code", "birth_date", "gender", "seniority_date", "coverage_scope", "premium", "coverages", "conditions", "source_page", "warnings"]}


def schema_conditions():
    return {"type": "object", "properties": {
        "policy_conditions": {"type": "array", "items": condition_schema()},
        "warnings": {"type": "array", "items": warning_schema()}},
        "required": ["policy_conditions", "warnings"]}


def schema_condition_section():
    return {"type": "object", "properties": {
        "condition": condition_schema(),
        "warnings": {"type": "array", "items": warning_schema()}},
        "required": ["condition", "warnings"]}


def _scalar_strings(value) -> list[str]:
    if isinstance(value, dict):
        return [item for key, child in value.items() if key != "notes" for item in _scalar_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _scalar_strings(child)]
    if isinstance(value, str):
        return [norm(value)] if norm(value) else []
    return []


def validate_notes(value, path: str = "$") -> list[str]:
    """Return errors for unsafe or duplicative notes anywhere in a model object."""
    errors = []
    if isinstance(value, dict):
        note = value.get("notes")
        if isinstance(note, str):
            note_path = f"{path}.notes"
            if not note.strip():
                errors.append(f"{note_path} must be null when there is no additional information")
            if len(note) > NOTES_MAX_LENGTH:
                errors.append(f"{note_path} exceeds {NOTES_MAX_LENGTH} characters")
            if "|" in note or re.search(r"(?:^|\n)\s*:?-{3,}:?\s*(?:\||$)", note):
                errors.append(f"{note_path} contains table or separator artifacts")
            if re.search(r"[\r\n\t]|\s{2,}", note):
                errors.append(f"{note_path} contains page formatting or repeated whitespace")

            note_key = keytext(note)
            for structured_value in _scalar_strings(value):
                structured_key = keytext(structured_value)
                if len(structured_key) >= 8 and structured_key in note_key:
                    errors.append(
                        f"{note_path} duplicates structured value {structured_value!r}"
                    )
                    break
        for key, child in value.items():
            if key != "notes":
                errors.extend(validate_notes(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(validate_notes(child, f"{path}[{index}]"))
    return errors


def validate_structured_response(value: object, schema: dict) -> None:
    errors = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(value)
    ]
    errors.extend(validate_notes(value))
    if errors:
        raise ValueError("; ".join(errors[:10]))


def call_structured(model: str, schema: dict, prompt: str, config: PipelineConfig, debug_dir: Path, tag: str = "call") -> dict:
    last = None
    retry_issue = None
    for attempt in range(1, config.ollama_retries + 2):
        content = ""
        try:
            attempt_prompt = prompt
            if retry_issue:
                attempt_prompt += (
                    "\n\nYour previous response for this same object failed validation. "
                    f"Correct only this object and return it again. Validation error: {retry_issue}"
                )
                if ".notes" in retry_issue:
                    attempt_prompt += (
                        "\nFor every notes field named in that error, set notes to null. "
                        "Do not move the duplicated text into another notes field or explain the correction in warnings."
                    )
            response = chat(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": attempt_prompt}],
                format=schema,
                think=False,
                options={
                    "temperature": config.ollama_temperature,
                    "num_predict": config.ollama_num_predict,
                    "num_ctx": config.ollama_num_ctx,
                },
            )
            content = response.message.content
            parsed = json.loads(content)
            validate_structured_response(parsed, schema)
            return parsed
        except Exception as exc:
            last = exc
            retry_issue = str(exc)
            # Preserve the exact raw model content for every rejected attempt.
            (debug_dir / f"debug_{tag}_attempt_{attempt}.txt").write_text(content, encoding="utf-8")
            if attempt <= config.ollama_retries:
                continue
    raise RuntimeError(f"{tag} failed after retries: {last}")


def build_page_evidence(doc: dict) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = defaultdict(list)
    for page_no, items in build_page_items(doc).items():
        pages[page_no] = [format_page_item(item) for item in items]
    return dict(sorted(pages.items()))


def page_sort_y(prov: dict) -> float:
    bbox = prov.get("bbox") or {}
    return float(max(bbox.get("t", 0), bbox.get("b", 0)))


def build_page_items(doc: dict) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = defaultdict(list)
    for item in doc.get("texts", []):
        text = clean_evidence_text(item.get("text") or item.get("orig") or "")
        prov = item.get("prov") or []
        page_no = prov[0].get("page_no") if prov else None
        if text and page_no:
            pages[int(page_no)].append({
                "kind": "text",
                "label": item.get("label") or "text",
                "text": text,
                "sort_y": page_sort_y(prov[0]),
                "sort_idx": len(pages[int(page_no)]),
            })
    for idx, table in enumerate(doc.get("tables", [])):
        prov = table.get("prov") or []
        page_no = prov[0].get("page_no") if prov else None
        if not page_no:
            continue
        grid = (table.get("data") or {}).get("grid") or []
        rows = []
        for row in grid:
            vals = []
            for column, cell in enumerate(row, start=1):
                raw = (cell.get("text") or "") if isinstance(cell, dict) else str(cell)
                cleaned = clean_evidence_text(raw)
                if cleaned:
                    # Keep original column coordinates while omitting empty cells.
                    vals.append(f"C{column}: {cleaned}")
            if vals:
                rows.append(" ; ".join(vals))
        if rows:
            pages[int(page_no)].append({
                "kind": "table",
                "label": f"TABLE {idx}",
                "text": "\n".join(rows),
                "sort_y": page_sort_y(prov[0]),
                "sort_idx": len(pages[int(page_no)]),
            })
    ordered = {}
    for page_no, items in pages.items():
        ordered[page_no] = sorted(items, key=lambda item: (-item["sort_y"], item["sort_idx"]))
    return dict(sorted(ordered.items()))


def clean_evidence_text(text: str | None) -> str:
    """Remove Docling blank/table residue without discarding meaningful text."""
    cleaned_lines = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\|\s*)+", line):
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?", line):
            continue
        if "|" in line:
            cells = [norm(cell) for cell in line.split("|") if norm(cell)]
            if not cells:
                continue
            line = " ; ".join(cells)
        if not cleaned_lines or line != cleaned_lines[-1]:
            cleaned_lines.append(line)
    return " ".join(cleaned_lines)


def format_page_item(item: dict) -> str:
    return f"[{item['label']}] {item['text']}"


def render_pages(pages: dict[int, list[str]], nums: list[int]) -> str:
    out = []
    for page_no in nums:
        if page_no in pages:
            out.append(f"\n===== PAGE {page_no} =====")
            out.extend(pages[page_no])
    return "\n".join(out)


def flat_page_text(pages: dict[int, list[str]], page_no: int) -> str:
    return "\n".join(pages.get(page_no, []))


def page_text_from_items(items: list[dict]) -> str:
    return "\n".join(item.get("text", "") for item in items)


def canonical_condition_heading(item: dict) -> str | None:
    """Identify a condition heading structurally, without a name allow-list."""
    label = item.get("label")
    if label not in {"section_header", "list_item"}:
        return None
    clean = re.sub(r"^[\-\*•−–]+\s*", "", norm(item.get("text")))
    keyed = keytext(clean)
    if not keyed or keyed in CONDITION_CHROME:
        return None
    if re.search(r"\d|[$%]", clean):
        return None
    if label == "list_item" and (len(clean) > 140 or clean.endswith(".")):
        return None
    return clean


def render_page_items(items: list[dict]) -> str:
    return "\n".join(format_page_item(item) for item in items)


def split_condition_sections(page_items: dict[int, list[dict]], page_no: int) -> list[dict]:
    items = page_items.get(page_no, [])
    marker_index = next(
        (index for index, item in enumerate(items) if keytext(item.get("text")) == "condiciones especiales"),
        None,
    )
    continuation = marker_index is None
    if continuation and not any(canonical_condition_heading(item) for item in items if item.get("label") == "list_item"):
        return []
    heading_positions = []
    for idx, item in enumerate(items):
        if marker_index is not None and idx <= marker_index:
            continue
        if continuation and item.get("label") != "list_item":
            continue
        heading = canonical_condition_heading(item)
        if heading:
            heading_positions.append((idx, heading))

    sections = []
    for pos, (start_idx, heading) in enumerate(heading_positions):
        end_idx = heading_positions[pos + 1][0] if pos + 1 < len(heading_positions) else len(items)
        chunk = items[start_idx:end_idx]
        sections.append({
            "page_no": page_no,
            "heading": heading,
            "items": chunk,
            "evidence": render_page_items(chunk),
        })
    return sections


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", keytext(text)).strip("_") or "section"


def condition_section_is_empty(condition: dict | None) -> bool:
    if not condition:
        return True
    if condition.get("condition_type") or condition.get("scope") or condition.get("description"):
        return False
    return len(condition.get("rules") or []) == 0


def failure_warning_for_section(page_no: int, heading: str, issue: str) -> dict:
    return {
        "field": "policy_conditions.section_extraction",
        "issue": f'Condition section "{heading}" on page {page_no} failed: {issue}',
        "severity": "high",
        "source_page": page_no,
    }


def extract_condition_sections(
    final: dict,
    page_items: dict[int, list[dict]],
    insured_blocks: list[dict],
    config: PipelineConfig,
    run_dir: Path,
) -> list[dict]:
    reports = []
    for block in insured_blocks:
        insured_number = block["insured_number"]
        for page_no in block.get("condition_pages", []):
            sections = split_condition_sections(page_items, page_no)
            page_report = {
                "page": page_no,
                "insured_number": insured_number,
                "detected_headings": [section["heading"] for section in sections],
                "extracted_headings": [],
                "failed_headings": [],
            }
            if not sections:
                final["validation"]["warnings"].append(failure_warning_for_section(page_no, "unknown", "No recognized condition headings were found on the page."))
                page_report["failed_headings"].append("unknown")
                reports.append(page_report)
                continue

            for section in sections:
                heading = section["heading"]
                tag = f"conditions_{page_no}_{slugify(heading)}"
                try:
                    extracted = call_structured(
                        config.model_name,
                        schema_condition_section(),
                        f"""Extract ONLY the GNP condition section with heading "{heading}" from this evidence.
Return exactly one condition object for this heading.
Do not include facts from other headings.
Preserve every explicit row or bullet as rules when present.
If the section contains insured-specific waiting-period dates, preserve them exactly.
EVIDENCE:\n===== PAGE {page_no} / SECTION {heading} =====\n{section['evidence']}""",
                        config=config,
                        debug_dir=run_dir,
                        tag=tag,
                    )
                except Exception as exc:
                    final["validation"]["warnings"].append(failure_warning_for_section(page_no, heading, str(exc)))
                    page_report["failed_headings"].append(heading)
                    continue

                condition = extracted.get("condition")
                if condition_section_is_empty(condition):
                    final["validation"]["warnings"].append(failure_warning_for_section(page_no, heading, "The model returned an empty condition payload."))
                    page_report["failed_headings"].append(heading)
                    final["validation"]["warnings"].extend(extracted.get("warnings", []))
                    continue

                condition["source_page"] = page_no
                condition["applicability_scope"] = "Insured"
                condition["applies_to_insured_numbers"] = [insured_number]
                condition["source_pages"] = [page_no]
                final["policy_conditions"].append(condition)
                final["validation"]["warnings"].extend(extracted.get("warnings", []))
                page_report["extracted_headings"].append(heading)
            reports.append(page_report)
    return reports


def certificate_heading_page(items: list[dict]) -> bool:
    return "CERTIFICADO DE COBERTURA POR ASEGURADO" in page_text_from_items(items).upper()


def detect_insured_anchor(items: list[dict]) -> dict | None:
    if not certificate_heading_page(items):
        return None
    candidates = [item.get("text", "") for item in items if item.get("label") == "section_header"]
    candidates.extend(item.get("text", "") for item in items if item.get("label") != "section_header")
    for text in candidates:
        match = re.search(r"\bAsegurado\s+(\d+)(?:\s*\(([^)]+)\))?\b", text, re.I)
        if match:
            return {"insured_number": int(match.group(1)), "role_hint": norm(match.group(2)) or None}
    return None


def discover_insured_certificate_blocks(page_items: dict[int, list[dict]]) -> list[dict]:
    anchors = []
    for page_no in sorted(page_items):
        anchor = detect_insured_anchor(page_items.get(page_no, []))
        if anchor:
            anchors.append({"insured_number": anchor["insured_number"], "role_hint": anchor["role_hint"], "header_page": page_no})

    deduped = []
    seen_numbers = set()
    for anchor in anchors:
        if anchor["insured_number"] in seen_numbers:
            continue
        seen_numbers.add(anchor["insured_number"])
        deduped.append(anchor)

    if not deduped:
        return []

    all_pages = sorted(page_items)
    max_page = all_pages[-1]
    blocks = []
    for idx, anchor in enumerate(deduped):
        start_page = anchor["header_page"]
        end_page = deduped[idx + 1]["header_page"] - 1 if idx + 1 < len(deduped) else max_page
        block_pages = [page_no for page_no in all_pages if start_page <= page_no <= end_page]
        condition_pages = [page_no for page_no in block_pages if split_condition_sections(page_items, page_no)]
        blocks.append({
            "insured_number": anchor["insured_number"],
            "role_hint": anchor["role_hint"],
            "header_page": start_page,
            "pages": block_pages or [start_page],
            "condition_pages": condition_pages,
        })
    return blocks


def empty_final() -> dict:
    return {
        "document": {"document_type": None, "language": None, "page_count": None},
        "policy": {"insurer": None, "policy_number": None, "version": None, "renewal_number": None, "product_line": None, "plan_raw_text": None, "plan_name": None, "hospital_level": None, "scheme_name": None, "issue_date": None, "coverage_start_date": None, "coverage_end_date": None, "term_days": None, "currency": None, "movement_type": None, "movement_description": None, "source_page": None},
        "policyholder": {"name": None, "customer_code": None, "tax_id": None, "address": None, "email": None, "phone": None, "source_page": None},
        "premium_summary": {"net_premium": None, "installment_surcharge": None, "policy_fee": None, "tax_rate_percent": None, "tax_amount": None, "total_amount": None, "payment_method": None, "payment_channel": None, "source_page": None},
        "agent": {"name": None, "agent_code": None, "source_page": None},
        "insureds": [],
        "policy_conditions": [],
        "regulatory": {"registration_number": None, "registration_date": None, "source_page": None},
        "document_sections": [],
        "condition_heading_audit": [],
        "validation": {"warnings": []},
    }


def extract_policy_from_docling(doc: dict, config: PipelineConfig, run_dir: Path) -> tuple[dict, dict]:
    page_items = build_page_items(doc)
    pages = build_page_evidence(doc)
    if not pages:
        raise RuntimeError("No page-aware evidence found in Docling JSON.")

    max_page = max(pages)
    final = empty_final()
    reports = {
        "page_count": len(pages),
        "insured_count": 0,
        "condition_page_count": 0,
        "condition_pages": [],
    }

    meta = call_structured(
        config.model_name,
        schema_policy_meta(),
        f"""Extract policy-level information only.
'Línea Azul' is a product line if presented that way; do not automatically use it as plan_name.
Preserve the full explicit Plan text in plan_raw_text. Extract regulatory registration only if explicit.
EVIDENCE:\n{render_pages(pages, [1])}""",
        config=config,
        debug_dir=run_dir,
        tag="meta",
    )
    for key in ("document", "policy", "policyholder", "premium_summary", "agent", "regulatory"):
        final[key] = meta[key]
    final["document"]["page_count"] = max_page
    final["validation"]["warnings"].extend(meta.get("warnings", []))

    blocks = discover_insured_certificate_blocks(page_items)
    if not blocks:
        final["validation"]["warnings"].append({
            "field": "insureds",
            "issue": "No insured certificate blocks automatically detected.",
            "severity": "high",
            "source_page": None,
        })
    seen_insured_numbers = set()
    for block in blocks:
        num = block["insured_number"]
        page_nums = block["pages"]
        insured = call_structured(
            config.model_name,
            schema_insured(),
            f"""Extract ONLY insured number {num} from this certificate block.
Coverage values must stay on the same logical row. "500.00 por servicio" is a service cost when tied to a service such as Membresía Médica Móvil, not a deductible unless explicitly labeled so.
For Emergencia Médica en el Extranjero, do not copy Nacional deductible/coinsurance.
conditions[] should be empty unless a condition is explicitly unique to this insured.
EVIDENCE:\n{render_pages(pages, page_nums)}""",
            config=config,
            debug_dir=run_dir,
            tag=f"insured_{num}",
        )
        warnings = insured.pop("warnings", [])
        if num in seen_insured_numbers:
            final["validation"]["warnings"].append({
                "field": "insureds",
                "issue": f"Duplicate insured certificate discovered for insured_number={num}.",
                "severity": "high",
                "source_page": block["header_page"],
            })
            continue
        seen_insured_numbers.add(num)
        insured["source_page"] = block["header_page"]
        insured["certificate_pages"] = page_nums
        insured["condition_pages"] = block.get("condition_pages", [])
        if block.get("role_hint") and not insured.get("role"):
            insured["role"] = block["role_hint"]
        final["insureds"].append(insured)
        final["validation"]["warnings"].extend(warnings)

    reports["condition_pages"] = extract_condition_sections(final, page_items, blocks, config, run_dir)

    codes = [row.get("customer_code") for row in final["insureds"] if row.get("customer_code")]
    if len(codes) != len(set(codes)):
        final["validation"]["warnings"].append({
            "field": "insureds.customer_code",
            "issue": "Duplicate customer codes detected.",
            "severity": "high",
            "source_page": None,
        })
    totals = [row.get("premium", {}).get("total_amount") for row in final["insureds"] if row.get("premium", {}).get("total_amount") is not None]
    policy_total = final.get("premium_summary", {}).get("total_amount")
    if totals and policy_total is not None and abs(sum(totals) - policy_total) > 0.05:
        final["validation"]["warnings"].append({
            "field": "premium_summary.total_amount",
            "issue": f"Sum of insured premiums ({sum(totals):.2f}) does not match policy total ({policy_total:.2f}).",
            "severity": "high",
            "source_page": 1,
        })

    reports["insured_count"] = len(final["insureds"])
    reports["condition_page_count"] = sum(len(block.get("condition_pages", [])) for block in blocks)
    return final, reports
