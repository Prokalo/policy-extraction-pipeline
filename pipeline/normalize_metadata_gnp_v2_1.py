#!/usr/bin/env python3
import argparse, json, re
from datetime import date
from pathlib import Path
import pymupdf

def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()

def iso_ymd(d,m,y):
    return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"

def money_pat():
    return r"[\d,]+(?:\.\d+)?"

def parse_money(raw):
    m = re.search(r"([\d,]+(?:\.\d+)?)", raw or "")
    return float(m.group(1).replace(",", "")) if m else None

def extract_page1(text):
    t = norm(text)
    out = {}

    m = re.search(r"Fecha de Expedici[oó]n\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4})", t, re.I)
    if m:
        out["issue_date"] = iso_ymd(*m.groups())

    m = re.search(
        r"Vigencia de la P[oó]liza.*?"
        r"Desde las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?"
        r"Hasta las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?"
        r"Duraci[oó]n\s+(\d+)\s+d[ií]as",
        t, re.I
    )
    if not m:
        m = re.search(
            r"Desde las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?"
            r"Hasta las 12 hrs\. del\s+(\d{1,2})\s+(\d{1,2})\s+(\d{4}).*?"
            r"Duraci[oó]n\s+(\d+)\s+d[ií]as",
            t, re.I
        )
    if m:
        d1,m1,y1,d2,m2,y2,days = m.groups()
        out["coverage_start_date"] = iso_ymd(d1,m1,y1)
        out["coverage_end_date"] = iso_ymd(d2,m2,y2)
        out["term_days"] = int(days)

    if re.search(r"Conducto de pago\s+Intermediario", t, re.I):
        out["payment_channel"] = "Intermediario"

    m = re.search(r"Forma de pago\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)", t, re.I)
    if m:
        out["payment_method"] = m.group(1)

    if re.search(r"Moneda\s+Nacional", t, re.I):
        out["currency"] = "MXN"

    premium_block = t
    pm = re.search(r"Prima de la P[oó]liza(.*?)(?:Descripci[oó]n del Movimiento|Asegurado \(s\))", t, re.I)
    if pm:
        premium_block = pm.group(1)

    fields = {
        "net_premium": rf"Prima Neta\s+({money_pat()})",
        "installment_surcharge": rf"Fraccionado\s+({money_pat()})",
        "policy_fee": rf"Derecho de P[oó]liza\s+({money_pat()})",
        "tax_amount": rf"I\.V\.A\.\s*16%\s+({money_pat()})",
        "total_amount": rf"Importe Total a\s+Pagar\s+({money_pat()})",
    }
    for field, pat in fields.items():
        m = re.search(pat, premium_block, re.I)
        if m:
            out[field] = parse_money(m.group(1))

    m = re.search(r"I\.V\.A\.\s*(\d+(?:\.\d+)?)%", premium_block, re.I)
    if m:
        out["tax_rate_percent"] = float(m.group(1))

    m = re.search(r"Clave\s*:?\s*(\d{10})", t, re.I)
    if m:
        out["agent_code"] = m.group(1)

    return out

def add_warning(data, field, issue, severity="low", source_page=1):
    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": field, "issue": issue, "severity": severity, "source_page": source_page
    })

def remove_stale(data):
    stale = {
        "coverage_start_date","coverage_end_date","term_days","premium_summary",
        "premium_summary.payment_channel","premium_summary.payment_method",
        "agent.agent_code","issue_date"
    }
    warnings = data.setdefault("validation", {}).setdefault("warnings", [])
    kept = [w for w in warnings if w.get("field") not in stale]
    removed = len(warnings) - len(kept)
    data["validation"]["warnings"] = kept
    return removed

def apply(data, parsed):
    policy = data.setdefault("policy", {})
    premium = data.setdefault("premium_summary", {})
    agent = data.setdefault("agent", {})

    for f in ("issue_date","coverage_start_date","coverage_end_date","term_days","currency"):
        if f in parsed:
            policy[f] = parsed[f]

    for f in ("net_premium","installment_surcharge","policy_fee","tax_rate_percent",
              "tax_amount","total_amount","payment_method","payment_channel"):
        if f in parsed:
            premium[f] = parsed[f]
    premium["source_page"] = 1

    if parsed.get("agent_code"):
        agent["agent_code"] = parsed["agent_code"]
    agent["source_page"] = 1

def validate(data):
    p = data.get("premium_summary", {})
    req = ["net_premium","installment_surcharge","policy_fee","tax_amount","total_amount"]
    missing = [x for x in req if p.get(x) is None]
    if missing:
        add_warning(data, "premium_summary", f"Missing deterministic premium field(s): {missing}", "high")
        return

    subtotal = float(p["net_premium"]) + float(p["installment_surcharge"]) + float(p["policy_fee"])
    rate = float(p.get("tax_rate_percent",16))
    expected_tax = round(subtotal * rate/100, 2)
    expected_total = round(subtotal + float(p["tax_amount"]), 2)

    if abs(expected_tax - float(p["tax_amount"])) > 0.02:
        add_warning(data, "premium_summary.tax_amount",
                    f"Tax arithmetic mismatch: expected {expected_tax:.2f}, document {float(p['tax_amount']):.2f}.",
                    "medium")
    if abs(expected_total - float(p["total_amount"])) > 0.02:
        add_warning(data, "premium_summary.total_amount",
                    f"Total arithmetic mismatch: calculated {expected_total:.2f}, document {float(p['total_amount']):.2f}.",
                    "high")

    policy = data.get("policy", {})
    if policy.get("coverage_start_date") and policy.get("coverage_end_date") and policy.get("term_days") is not None:
        try:
            days = (date.fromisoformat(policy["coverage_end_date"]) - date.fromisoformat(policy["coverage_start_date"])).days
            if days != int(policy["term_days"]):
                add_warning(data, "term_days",
                            f"Coverage dates imply {days} days but document states {policy['term_days']}.",
                            "medium")
        except Exception:
            add_warning(data, "policy", "Invalid normalized ISO policy dates.", "high")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_json")
    ap.add_argument("pdf")
    ap.add_argument("--output", default="insurance_v3_final.json")
    args = ap.parse_args()

    data = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    pdf = pymupdf.open(args.pdf)
    parsed = extract_page1(pdf[0].get_text("text"))

    removed = remove_stale(data)
    apply(data, parsed)
    validate(data)

    data.setdefault("validation", {}).setdefault("warnings", []).append({
        "field": "metadata",
        "issue": "GNP metadata normalizer v2 applied deterministic page-1 normalization.",
        "severity": "low",
        "source_page": 1
    })

    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    p = data.get("policy", {})
    s = data.get("premium_summary", {})
    a = data.get("agent", {})
    print("GNP METADATA NORMALIZATION")
    print(f"Issue date: {p.get('issue_date')}")
    print(f"Coverage: {p.get('coverage_start_date')} -> {p.get('coverage_end_date')}")
    print(f"Term days: {p.get('term_days')}")
    print(f"Payment channel: {s.get('payment_channel')}")
    print(f"Payment method: {s.get('payment_method')}")
    print(f"Net premium: {s.get('net_premium')}")
    print(f"Installment surcharge: {s.get('installment_surcharge')}")
    print(f"Policy fee: {s.get('policy_fee')}")
    print(f"Tax: {s.get('tax_amount')}")
    print(f"Total: {s.get('total_amount')}")
    print(f"Agent code: {a.get('agent_code')}")
    print(f"Stale metadata warnings removed: {removed}")
    print(f"Saved: {args.output}")

if __name__ == "__main__":
    main()
