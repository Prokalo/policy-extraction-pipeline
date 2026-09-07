from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from docling.document_converter import DocumentConverter
from ollama import chat

from pipeline.config import PipelineConfig


SYSTEM_PROMPT = """You are a strict Spanish insurance-policy extraction engine.
Extract only facts explicitly supported by the supplied Docling evidence. Never invent; if uncertain return null.
Preserve names, RFC/tax IDs, policy numbers, customer codes, Spanish plan names and coverage names exactly.
Normalize pesos -> MXN and dls/dólares norteamericanos -> USD only in normalized currency fields.
Never borrow sum insured, deductible, coinsurance, or service cost from an adjacent row. Values must belong to the same logical coverage row.
If row association is uncertain, return null and add a warning. Preserve every row of rule tables. Return JSON matching the supplied schema."""

CONDITION_HEADINGS = {
    "cobertura de preexistencia": "Cobertura de preexistencia",
    "cobertura de atencion en el extranjero": "Cobertura de atención en el extranjero",
    "tope de coaseguro": "Tope de coaseguro",
    "eliminacion o reduccion de periodos de espera": "Eliminación o reducción de periodos de espera",
    "monto para productos de terapia genica": "Monto para Productos de Terapia génica",
    "auxiliares mecanicos electronicos y o computarizados": "Auxiliares mecánicos electrónicos y/o computarizados",
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
            "notes": {"type": ["string", "null"]}},
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
        "notes": {"type": ["string", "null"]}, "source_page": {"type": ["integer", "null"]}},
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


def call_structured(model: str, schema: dict, prompt: str, config: PipelineConfig, debug_dir: Path, tag: str = "call") -> dict:
    last = None
    for attempt in range(1, config.ollama_retries + 2):
        try:
            response = chat(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                format=schema,
                think=False,
                options={
                    "temperature": config.ollama_temperature,
                    "num_predict": config.ollama_num_predict,
                    "num_ctx": config.ollama_num_ctx,
                },
            )
            content = response.message.content
            return json.loads(content)
        except Exception as exc:
            last = exc
            raw = locals().get("content", "")
            (debug_dir / f"debug_{tag}_attempt_{attempt}.txt").write_text(raw, encoding="utf-8")
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
        text = (item.get("text") or item.get("orig") or "").strip()
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
            for cell in row:
                vals.append(((cell.get("text") or "") if isinstance(cell, dict) else str(cell)).strip())
            rows.append(" | ".join(vals))
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


def canonical_condition_heading(text: str) -> str | None:
    clean = re.sub(r"^[\-\*\u2022]+\s*", "", norm(text))
    return CONDITION_HEADINGS.get(keytext(clean))


def render_page_items(items: list[dict]) -> str:
    return "\n".join(format_page_item(item) for item in items)


def split_condition_sections(page_items: dict[int, list[dict]], page_no: int) -> list[dict]:
    items = page_items.get(page_no, [])
    heading_positions = []
    for idx, item in enumerate(items):
        heading = canonical_condition_heading(item.get("text"))
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


def find_condition_pages(pages: dict[int, list[str]]) -> list[int]:
    keys = (
        "CONDICIONES ESPECIALES",
        "COBERTURA DE PREEXISTENCIA",
        "TOPE DE COASEGURO",
        "PERIODOS DE ESPERA",
        "PENALIZACIÓN",
        "PENALIZACION",
        "TERAPIA GÉNICA",
        "TERAPIA GENICA",
    )
    out = []
    for page_no in sorted(pages):
        text = flat_page_text(pages, page_no)
        upper = text.upper()
        if not any(key in upper for key in keys):
            continue
        out.append(page_no)
    return out


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
