#!/usr/bin/env python3
"""
GNP deterministic coverage repair v2.

Supports the two GNP layouts tested so far:
- Flexible / Ámbar style certificate
- Premier style certificate

Input:
    extracted_json
    source_pdf

Output:
    insurance_v3_gnp_repaired.json

What it does:
1. Detects GNP and the plan/layout family.
2. Rebuilds insured certificate coverages from native PDF word geometry.
3. Rebuilds page-1 policy_coverages separately.
4. Never copies policy-level rows into insured certificate rows.
5. Preserves raw values such as "Sin Límite" and "No aplica".
"""

import argparse
import copy
import json
import re
import unicodedata
from pathlib import Path

import pymupdf


# ---------------------------------------------------------------------
# Canonical GNP coverage labels observed across tested layouts.
# Keep this insurer-specific. Add new labels as future GNP layouts appear.
# ---------------------------------------------------------------------
CERTIFICATE_COVERAGES = [
    ("Nacional", "Básicas"),
    ("Asistencia en Viajes", "Básicas"),
    ("Membresía de Médica Móvil", "Básicas"),
    ("Enfermedades Catastróficas Nacional", "Básicas"),
    ("Emergencia Médica en el Extranjero", "Opcionales"),
    ("Cero Deducible por Accidente", "Opcionales"),
    ("Ampliación Hospitalaria Definida a PREMIUM", "Opcionales"),
]

POLICY_ONLY_COVERAGES = [
    ("Emergencia de gastos médicos mayores no cubiertos - Nacional", "Básicas"),
]


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def deaccent(s):
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def keytext(s):
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(s)).lower()).strip()


def money(raw):
    raw = norm(raw)
    if not raw:
        return {"raw_value": None, "amount": None, "currency": None}

    # Non-numeric contractual values such as "Sin Límite".
    if not re.search(r"\d", raw):
        return {"raw_value": raw, "amount": None, "currency": None}

    m = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    amount = float(m.group(1).replace(",", "")) if m else None
    low = deaccent(raw).lower()

    if "dls" in low or "usd" in low or "dolar" in low:
        currency = "USD"
    elif "peso" in low:
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

    m = re.search(r"(\d+(?:\.\d+)?)\s*%", raw)
    if m:
        return {
            "raw_value": raw,
            "percentage": float(m.group(1)),
            "applies": True,
        }

    return {"raw_value": raw, "percentage": None, "applies": None}


def service_cost(raw):
    raw = norm(raw)
    if not raw:
        return {"raw_value": None, "amount": None, "currency": None, "unit": None}

    if "por servicio" not in deaccent(raw).lower():
        return {"raw_value": None, "amount": None, "currency": None, "unit": None}

    m = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    amount = float(m.group(1).replace(",", "")) if m else None
    return {
        "raw_value": raw,
        "amount": amount,
        "currency": "MXN",
        "unit": "por servicio",
    }


def empty_money():
    return {"raw_value": None, "amount": None, "currency": None}


def empty_coinsurance():
    return {"raw_value": None, "percentage": None, "applies": None}


def empty_service_cost():
    return {"raw_value": None, "amount": None, "currency": None, "unit": None}


def normalize_status(raw):
    raw = norm(raw)
    low = deaccent(raw).lower()
    if "amparada" in low:
        return "Amparada"
    return None


def group_visual_lines(page, tol=0.8):
    """
    Group native PDF words into visual horizontal lines by Y coordinate,
    independent of PDF block/line IDs.
    """
    words = sorted(page.get_text("words"), key=lambda w: (w[1], w[0]))
    lines = []

    for w in words:
        y = w[1]
        target = None
        for line in reversed(lines[-4:]):
            if abs(line["y"] - y) <= tol:
                target = line
                break

        if target is None:
            target = {"y": y, "words": []}
            lines.append(target)

        target["words"].append(w)
        target["y"] = sum(x[1] for x in target["words"]) / len(target["words"])

    for line in lines:
        line["words"].sort(key=lambda w: w[0])
        line["text"] = norm(" ".join(w[4] for w in line["words"]))

    lines.sort(key=lambda x: x["y"])
    return lines


def find_certificate_region(page):
    """
    Return y_start, y_end for the certificate coverage table.
    """
    lines = group_visual_lines(page)
    y_start = None
    y_end = None

    for line in lines:
        k = keytext(line["text"])
        if y_start is None and k in {"basicas", "basica"}:
            # Prefer the category header that occurs after Coberturas y Servicios.
            if line["y"] > 220:
                y_start = line["y"]
                continue

        if y_start is not None:
            if (
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
    """
    GNP certificate geometry observed in both tested layouts:
      coverage/name   x < 155
      sum insured     155 <= x < 270
      deductible      270 <= x < 365
      coinsurance     365 <= x < 420
    Ignore right-side premium/version panel x >= 420.
    """
    cells = {"name": [], "sum": [], "ded": [], "coins": []}

    for w in line["words"]:
        x = w[0]
        if x >= 420:
            continue
        if x < 155:
            cells["name"].append(w[4])
        elif x < 270:
            cells["sum"].append(w[4])
        elif x < 365:
            cells["ded"].append(w[4])
        else:
            cells["coins"].append(w[4])

    return {k: norm(" ".join(v)) for k, v in cells.items()}


def region_lines(page):
    y_start, y_end = find_certificate_region(page)
    out = []

    for line in group_visual_lines(page):
        if not (y_start <= line["y"] < y_end):
            continue
        cells = split_columns(line)
        if any(cells.values()):
            out.append({
                "y": line["y"],
                **cells,
            })

    return out


def match_coverage_spans(lines):
    """
    Match known GNP coverage names against contiguous name-column text.
    This handles wrapped labels without guessing row ownership from values.
    """
    spans = []
    i = 0

    while i < len(lines):
        # Category/header lines are not coverage names.
        nk = keytext(lines[i]["name"])
        if nk in {
            "basicas", "basica", "opcionales",
            "coberturas y servicios", "suma asegurada",
            "deducible", "coaseguro"
        }:
            i += 1
            continue

        best = None

        # Try 1-3 consecutive visual lines for wrapped coverage names.
        for width in (1, 2, 3):
            if i + width > len(lines):
                continue

            candidate = norm(" ".join(lines[j]["name"] for j in range(i, i + width)))
            ck = keytext(candidate)

            for canonical, category in CERTIFICATE_COVERAGES:
                target = keytext(canonical)

                if ck == target:
                    score = 1000 + len(target)
                elif target.startswith(ck) and len(ck) >= 5:
                    score = 100 + len(ck)
                elif ck.startswith(target) and len(target) >= 5:
                    score = 100 + len(target)
                else:
                    score = -1

                if score >= 0 and (best is None or score > best["score"]):
                    best = {
                        "canonical": canonical,
                        "category": category,
                        "start": i,
                        "end": i + width - 1,
                        "score": score,
                    }

        if best and best["score"] >= 1000:
            spans.append(best)
            i = best["end"] + 1
        else:
            i += 1

    # If a row category was explicitly marked in the PDF, use it.
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
    """
    Combine values across all physical lines occupied by a wrapped coverage.
    """
    chunk = lines[span["start"]: span["end"] + 1]
    return {
        "sum": norm(" ".join(x["sum"] for x in chunk if x["sum"])),
        "ded": norm(" ".join(x["ded"] for x in chunk if x["ded"])),
        "coins": norm(" ".join(x["coins"] for x in chunk if x["coins"])),
    }


def coverage_from_span(lines, span, source_page):
    vals = combine_span_cells(lines, span)
    sum_raw = vals["sum"]
    ded_raw = vals["ded"]
    coins_raw = vals["coins"]

    # The GNP column boundary can split "No aplica" across the deductible
    # and coinsurance columns ("No" | "aplica"). Repair that deterministically.
    if keytext(ded_raw).endswith("no") and keytext(coins_raw) == "aplica":
        ded_raw = norm(re.sub(r"\bNo\s*$", "", ded_raw, flags=re.I))
        coins_raw = "No aplica"

    # "Amparada" can appear in the sum-insured column.
    status = normalize_status(sum_raw)

    # Do not interpret Amparada as monetary sum insured.
    sum_obj = empty_money() if status else money(sum_raw)

    # "500.00 por servicio" appears in the deductible column for Membresía.
    service_obj = service_cost(ded_raw)
    if service_obj["raw_value"] is not None:
        ded_obj = empty_money()
    else:
        ded_obj = money(ded_raw)

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
    rows = [coverage_from_span(lines, s, page_no) for s in spans]

    # Ensure deterministic ordering by vertical appearance.
    order = {s["canonical"]: s["start"] for s in spans}
    rows.sort(key=lambda r: order[r["name"]])
    return rows


# ---------------------------------------------------------------------
# Page 1 policy coverage summary
# ---------------------------------------------------------------------

def page1_block(page):
    text = norm(page.get_text("text"))
    m = re.search(
        r"Coberturas y Servicios(.*?)(?:El círculo médico|El circulo medico)",
        text,
        flags=re.I,
    )
    if not m:
        raise RuntimeError("Could not locate page-1 GNP policy coverage block.")
    return m.group(1)


def make_policy_coverage(
    name,
    category,
    sum_raw=None,
    ded_raw=None,
    coins_raw=None,
    status=None,
):
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
    """
    Deterministic text extraction from page-1 summary.
    Supports both tested GNP policy layouts.
    """
    block = page1_block(page)
    rows = []

    # Base Nacional:
    # Flexible layout -> numeric sum insured
    # Premier layout  -> "Sin Límite"
    m = re.search(
        r"(?:−|-)?\s*Nacional\s+"
        r"((?:Sin Límite)|(?:[\d,]+(?:\.\d+)?\s+pesos))\s+"
        r"([\d,]+(?:\.\d+)?\s+pesos)\s+"
        r"(\d+(?:\.\d+)?\s*%)",
        block,
        flags=re.I,
    )
    if m:
        rows.append(make_policy_coverage(
            "Nacional",
            "Básicas",
            sum_raw=m.group(1),
            ded_raw=m.group(2),
            coins_raw=m.group(3),
        ))

    # Emergency expenses not covered, Nacional.
    m = re.search(
        r"Emergencia de gastos médicos\s+mayores no cubiertos\s+"
        r"(?:−|-)?\s*Nacional\s+"
        r"([\d,]+(?:\.\d+)?\s+pesos)\s+"
        r"([\d,]+(?:\.\d+)?\s+pesos)\s+"
        r"(\d+(?:\.\d+)?\s*%)",
        block,
        flags=re.I,
    )
    if m:
        rows.append(make_policy_coverage(
            "Emergencia de gastos médicos mayores no cubiertos - Nacional",
            "Básicas",
            sum_raw=m.group(1),
            ded_raw=m.group(2),
            coins_raw=m.group(3),
        ))

    # Foreign emergency.
    m = re.search(
        r"Emergencia Médica en el\s+Extranjero\s+"
        r"([\d,]+(?:\.\d+)?\s+dls)\s+"
        r"([\d,]+(?:\.\d+)?\s+dls)\s+"
        r"(No aplica)",
        block,
        flags=re.I,
    )
    if m:
        rows.append(make_policy_coverage(
            "Emergencia Médica en el Extranjero",
            "Opcionales",
            sum_raw=m.group(1),
            ded_raw=m.group(2),
            coins_raw=m.group(3),
        ))

    # Status-only rows.
    status_specs = [
        ("Asistencia en Viajes", "Básicas", r"Asistencia en Viajes\s+Amparada"),
        ("Membresía Médica Móvil", "Básicas", r"Membresía Médica Móvil\s+Amparada"),
        ("Enfermedades Catastróficas Nacional", "Básicas",
         r"Enfermedades Catastróficas\s+Nacional\s+Amparada"),
        ("Cero Deducible por Accidente", "Opcionales",
         r"Cero Deducible por Accidente\s+Amparada"),
        ("Ampliación Hospitalaria Definida a PREMIUM", "Opcionales",
         r"Ampliación Hospitalaria Definida a\s+PREMIUM\s+Amparada"),
    ]

    existing = {keytext(r["name"]) for r in rows}
    for name, category, pattern in status_specs:
        if keytext(name) in existing:
            continue
        if re.search(pattern, block, flags=re.I):
            rows.append(make_policy_coverage(
                name,
                category,
                status="Amparada",
            ))

    return rows


# ---------------------------------------------------------------------
# Detection / merge / validation
# ---------------------------------------------------------------------

def detect_layout(data, pdf):
    insurer = keytext(data.get("policy", {}).get("insurer"))
    if "grupo nacional provincial" not in insurer:
        raise RuntimeError("This parser only supports GNP policies.")

    plan = norm(
        data.get("policy", {}).get("plan_name")
        or data.get("policy", {}).get("plan_raw_text")
    )
    pk = keytext(plan)

    if "premier" in pk:
        return "gnp_premier"
    if "flexible" in pk or "ambar" in pk:
        return "gnp_flexible"

    # Fallback to document evidence.
    page1 = keytext(pdf[0].get_text("text"))
    if "premier" in page1:
        return "gnp_premier"

    return "gnp_unknown"


def locate_insured_pages(data, pdf):
    pages = []

    for insured in data.get("insureds", []):
        p = insured.get("source_page")
        if isinstance(p, int) and 1 <= p <= len(pdf):
            pages.append((insured, p))

    # Fallback if source_page is absent.
    if not pages:
        for page_no, page in enumerate(pdf, start=1):
            text = page.get_text("text")
            m = re.search(r"Asegurado\s+(\d+)", text, flags=re.I)
            if not m:
                continue

            n = int(m.group(1))
            insured = next(
                (x for x in data.get("insureds", [])
                 if x.get("insured_number") == n),
                None
            )
            if insured:
                pages.append((insured, page_no))

    return pages


def clean_old_coverage_warnings(data):
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    cleaned = []
    removed = 0

    for w in warnings:
        field = w.get("field")
        issue = keytext(w.get("issue"))

        if field in {"insureds.coverages", "policy_coverages"} and (
            "coverage" in issue or "cobertura" in issue
        ):
            removed += 1
            continue

        cleaned.append(w)

    data["validation"]["warnings"] = cleaned
    return removed


def add_audit(data, layout, insured_counts, policy_count):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": "coverages",
        "issue": (
            f"GNP deterministic coverage parser v2 used layout '{layout}'. "
            f"Insured coverage counts={insured_counts}; "
            f"policy-level coverage count={policy_count}."
        ),
        "severity": "low",
        "source_page": None,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("pdf")
    ap.add_argument("--output", default="insurance_v3_gnp_repaired.json")
    args = ap.parse_args()

    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    pdf = pymupdf.open(args.pdf)

    layout = detect_layout(data, pdf)
    print(f"GNP layout detected: {layout}")

    insured_counts = {}
    insured_pages = locate_insured_pages(data, pdf)

    for insured, page_no in insured_pages:
        rows = extract_certificate_coverages(pdf[page_no - 1], page_no)
        insured["coverages"] = rows
        insured_counts[insured.get("insured_number")] = len(rows)

        print(
            f"Insured {insured.get('insured_number')}: "
            f"{len(rows)} deterministic coverage rows"
        )
        for row in rows:
            print(
                "  -",
                row["name"],
                "|",
                row["sum_insured"]["raw_value"],
                "|",
                row["deductible"]["raw_value"],
                "|",
                row["coinsurance"]["raw_value"],
                "|",
                row["service_cost"]["raw_value"],
                "|",
                row["status"],
            )

    policy_rows = extract_policy_coverages(pdf[0])
    data["policy_coverages"] = policy_rows

    print(f"\nPolicy-level coverages: {len(policy_rows)}")
    for row in policy_rows:
        print(
            "  -",
            row["name"],
            "|",
            row["sum_insured"]["raw_value"],
            "|",
            row["deductible"]["raw_value"],
            "|",
            row["coinsurance"]["raw_value"],
            "|",
            row["status"],
        )

    removed = clean_old_coverage_warnings(data)
    add_audit(data, layout, insured_counts, len(policy_rows))

    Path(args.output).write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\nOld coverage warnings removed: {removed}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
