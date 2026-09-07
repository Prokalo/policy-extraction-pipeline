#!/usr/bin/env python3
"""
GNP condition/document cleanup v2.

Input:
    insurance_v3_gnp_repaired.json
    source_pdf

Output:
    insurance_v3_clean_v2.json

Fixes learned from two GNP policies:
1. Keeps true policy rules in policy_conditions.
2. Routes insured waiting-period history to insured.conditions.
3. Canonicalizes/merges preexistence tables.
4. Corrects the Premier 400 footnote so it belongs to foreign-care coverage,
   not preexistence.
5. Extracts legal/admin text directly from PDF pages into document_sections.
6. Extracts regulatory registration directly from the PDF.
"""

import argparse
import copy
import json
import re
import unicodedata
from pathlib import Path

import pymupdf


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def deaccent(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def keytext(s):
    return re.sub(r"[^a-z0-9%$]+", " ", deaccent(norm(s)).lower()).strip()


def iso_date_es(day, month_name, year):
    months = {
        "enero":1, "febrero":2, "marzo":3, "abril":4, "mayo":5, "junio":6,
        "julio":7, "agosto":8, "septiembre":9, "octubre":10,
        "noviembre":11, "diciembre":12
    }
    m = months.get(deaccent(month_name).lower())
    if not m:
        return None
    return f"{int(year):04d}-{m:02d}-{int(day):02d}"


def canonical_type(raw):
    k = keytext(raw)

    aliases = {
        "periodo de cobertura": "Cobertura de preexistencia",
        "suma asegurada por periodo de cobertura": "Cobertura de preexistencia",
        "cobertura de preexistencia": "Cobertura de preexistencia",
        "region y coaseguro": "Cobertura de atención en el extranjero",
        "cobertura atencion en el extranjero": "Cobertura de atención en el extranjero",
        "cobertura de atencion en el extranjero": "Cobertura de atención en el extranjero",
        "tope de coaseguro": "Tope de coaseguro",
        "monto para productos de terapia genica": "Monto para Productos de Terapia génica",
        "eliminacion o reduccion de periodos de espera":
            "Eliminación o reducción de periodos de espera",
    }
    return aliases.get(k, norm(raw) or "Otra condición")


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
    seen = {rule_sig(r) for r in target.get("rules", [])}
    for r in incoming.get("rules", []):
        sig = rule_sig(r)
        if sig not in seen:
            target.setdefault("rules", []).append(copy.deepcopy(r))
            seen.add(sig)


def find_waiting_condition(conditions):
    for c in conditions:
        if canonical_type(c.get("condition_type")) == "Eliminación o reducción de periodos de espera":
            return c
    return None


def page_for_insured_condition(cond, insureds):
    p = cond.get("source_page")
    if not isinstance(p, int):
        return None
    candidates = []
    for ins in insureds:
        cp = ins.get("source_page")
        if isinstance(cp, int) and 0 < p - cp <= 2:
            candidates.append((p-cp, ins))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def clean_policy_conditions(data):
    raw = data.get("policy_conditions", [])
    policy = []
    by_type = {}
    moved_to_insured = 0

    for c in raw:
        c = copy.deepcopy(c)
        ctype = canonical_type(c.get("condition_type"))
        c["condition_type"] = ctype

        # Insured-specific waiting period history.
        if ctype == "Eliminación o reducción de periodos de espera":
            insured = page_for_insured_condition(c, data.get("insureds", []))
            if insured is not None:
                insured.setdefault("conditions", [])
                # replace prior waiting-period condition
                insured["conditions"] = [
                    x for x in insured["conditions"]
                    if canonical_type(x.get("condition_type")) != ctype
                ]
                c["scope"] = "Insured"
                insured["conditions"].append(c)
                moved_to_insured += 1
                continue

        # The Qwen extractor can attach "* Esta cobertura no aplica para Premier 400"
        # to preexistence. That footnote belongs to the foreign-care table.
        desc_key = keytext(c.get("description"))
        if (
            ctype == "Cobertura de preexistencia"
            and "no aplica para premier 400" in desc_key
            and len(c.get("rules", [])) <= 1
        ):
            c["condition_type"] = "Cobertura de atención en el extranjero"
            ctype = c["condition_type"]

        if ctype not in by_type:
            by_type[ctype] = c
            policy.append(c)
        else:
            target = by_type[ctype]
            merge_rules(target, c)

            d1 = norm(target.get("description"))
            d2 = norm(c.get("description"))
            if d2 and d2 not in d1:
                target["description"] = norm((d1 + " " + d2).strip())

    data["policy_conditions"] = policy
    return len(raw), len(policy), moved_to_insured


def add_section(data, section_type, heading, text, page):
    text = norm(text)
    if not text:
        return False

    existing = data.setdefault("document_sections", [])
    sig = (section_type, keytext(text)[:500])
    for s in existing:
        if (s.get("section_type"), keytext(s.get("text"))[:500]) == sig:
            return False

    existing.append({
        "section_type": section_type,
        "heading": heading,
        "text": text,
        "page_start": page,
        "page_end": page,
    })
    return True


def extract_document_sections_and_regulatory(data, pdf):
    added = 0
    reg_found = False

    full_pages = [(i+1, norm(p.get_text("text"))) for i, p in enumerate(pdf)]

    for page_no, text in full_pages:
        # Contract/document integration language.
        m = re.search(
            r"(Este documento forma parte integrante del Contrato de Seguro.*?"
            r"(?:Usuarios de Servicios Financieros\.|CONDUSEF\.))",
            text,
            flags=re.I
        )
        if m:
            added += add_section(
                data, "coverage_scope", "Alcance y documentos del contrato",
                m.group(1), page_no
            )

        # Privacy notice.
        m = re.search(
            r"(El tratamiento de los datos personales.*?"
            r"(?:55\s*5227[−\- ]?9000\.?))",
            text,
            flags=re.I
        )
        if m:
            added += add_section(
                data, "privacy_notice", "Aviso de privacidad",
                m.group(1), page_no
            )

        # UNE / CONDUSEF dispute resolution.
        m = re.search(
            r"(Para cualquier aclaración o duda no resuelta relacionada con su seguro.*?"
            r"(?:condusef\.gob\.mx\.?))",
            text,
            flags=re.I
        )
        if m:
            added += add_section(
                data, "dispute_resolution", "UNE / CONDUSEF",
                m.group(1), page_no
            )

        # Certificate delivery.
        m = re.search(
            r"(Los Certificados de todos y cada uno de los Asegurados.*?"
            r"(?:cada Asegurado\.))",
            text,
            flags=re.I
        )
        if m:
            added += add_section(
                data, "document_delivery", "Entrega de certificados",
                m.group(1), page_no
            )

        # Regulatory registration - supports both tested formats.
        reg_m = re.search(
            r"registradas ante la Comisión Nacional de Seguros y Fianzas.*?"
            r"(\d{1,2})\s+de\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\s+de\s+(\d{4}).*?"
            r"(CNSF[−\-][A-Z0-9−\-/]+(?:/CONDUSEF[−\-][A-Z0-9−\-]+)?)",
            text,
            flags=re.I
        )
        if reg_m:
            day, month, year, number = reg_m.groups()
            number = number.replace("−", "-")
            data["regulatory"] = {
                "registration_number": number,
                "registration_date": iso_date_es(day, month, year),
                "source_page": page_no,
            }
            reg_found = True

            add_section(
                data,
                "regulatory_registration",
                "Registro CNSF",
                reg_m.group(0),
                page_no,
            )

    return added, reg_found


def dedupe_sections(data):
    seen = set()
    out = []
    for s in data.get("document_sections", []):
        sig = (s.get("section_type"), keytext(s.get("text"))[:700])
        if sig not in seen:
            seen.add(sig)
            out.append(s)
    data["document_sections"] = out


def add_audit(data, raw_count, final_count, moved, sections, reg):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": "policy_conditions",
        "issue": (
            f"GNP condition cleanup v2: {raw_count} raw conditions -> "
            f"{final_count} policy-wide conditions; {moved} routed to "
            f"insured.conditions; {sections} document sections added; "
            f"regulatory registration found={reg}."
        ),
        "severity": "low",
        "source_page": None,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("pdf")
    ap.add_argument("--output", default="insurance_v3_clean_v2.json")
    args = ap.parse_args()

    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    pdf = pymupdf.open(args.pdf)

    raw_count, final_count, moved = clean_policy_conditions(data)
    sections_added, reg_found = extract_document_sections_and_regulatory(data, pdf)
    dedupe_sections(data)
    add_audit(data, raw_count, final_count, moved, sections_added, reg_found)

    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"Raw policy conditions: {raw_count}")
    print(f"Final policy-wide conditions: {final_count}")
    print(f"Moved to insured.conditions: {moved}")
    print(f"Document sections: {len(data.get('document_sections', []))}")
    print(f"Regulatory registration repaired: {reg_found}")
    for ins in data.get("insureds", []):
        print(
            f"Insured {ins.get('insured_number')}: "
            f"{len(ins.get('conditions', []))} insured-specific conditions"
        )
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
