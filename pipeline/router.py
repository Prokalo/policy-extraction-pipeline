from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any


DEFAULT_RAMO_SIGNALS_PATH = Path("config/router/ramo_signals.json")
UNKNOWN_RAMO = "UNKNOWN"


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def deaccent(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def keytext(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(text)).lower()).strip()


def load_ramo_signals(path: Path = DEFAULT_RAMO_SIGNALS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scalar_text_records(value: Any, path: str = "$") -> list[dict]:
    records = []
    if isinstance(value, dict):
        for key, child in value.items():
            records.extend(_scalar_text_records(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            records.extend(_scalar_text_records(child, f"{path}[{index}]"))
    elif isinstance(value, str) and norm(value):
        records.append({"source": "extracted", "path": path, "text": norm(value), "page": None})
    return records


def _docling_records(docling_data: dict | None) -> list[dict]:
    if not docling_data:
        return []
    records = []
    for index, item in enumerate(docling_data.get("texts") or []):
        text = norm(item.get("text") or item.get("orig"))
        if not text:
            continue
        prov = item.get("prov") or []
        page = prov[0].get("page_no") if prov else None
        records.append({"source": "docling", "path": f"$.texts[{index}]", "text": text, "page": page})
    for index, table in enumerate(docling_data.get("tables") or []):
        cells = []
        for row in ((table.get("data") or {}).get("grid") or []):
            for cell in row:
                if isinstance(cell, dict):
                    text = norm(cell.get("text"))
                else:
                    text = norm(str(cell))
                if text:
                    cells.append(text)
        if not cells:
            continue
        prov = table.get("prov") or []
        page = prov[0].get("page_no") if prov else None
        records.append({"source": "docling", "path": f"$.tables[{index}]", "text": " ".join(cells), "page": page})
    return records


def build_router_evidence(extracted_data: dict, docling_data: dict | None = None) -> list[dict]:
    return _scalar_text_records(extracted_data) + _docling_records(docling_data)


def _snippet(text: str, pattern: str) -> str:
    keyed_pattern = keytext(pattern)
    keyed_text = keytext(text)
    pos = keyed_text.find(keyed_pattern)
    if pos < 0:
        return norm(text)[:180]
    raw = norm(text)
    return raw[:180]


def _match_signal(signal: dict, evidence_records: list[dict]) -> list[dict]:
    matches = []
    for pattern in signal.get("patterns") or []:
        pattern_key = keytext(pattern)
        if not pattern_key:
            continue
        for record in evidence_records:
            if pattern_key not in keytext(record.get("text")):
                continue
            matches.append({
                "signal_id": signal.get("id"),
                "pattern": pattern,
                "weight": signal.get("weight", 0),
                "source": record.get("source"),
                "path": record.get("path"),
                "page": record.get("page"),
                "snippet": _snippet(record.get("text", ""), pattern),
            })
            break
    return matches


def score_document_ramo(
    extracted_data: dict,
    docling_data: dict | None = None,
    signals_config: dict | None = None,
) -> dict:
    config = signals_config or load_ramo_signals()
    evidence_records = build_router_evidence(extracted_data, docling_data)
    ramo_order = config.get("ramo_order") or sorted((config.get("signals") or {}).keys())
    scores = {}
    for ramo in ramo_order:
        ramo_score = 0.0
        matched_evidence = []
        for signal in (config.get("signals") or {}).get(ramo, []):
            matches = _match_signal(signal, evidence_records)
            if matches:
                ramo_score += float(signal.get("weight") or 0)
                matched_evidence.extend(matches)
        scores[ramo] = {
            "score": min(round(ramo_score, 2), 100.0),
            "evidence": matched_evidence,
        }
    return {
        "config_version": config.get("version"),
        "minimum_confidence": float(config.get("minimum_confidence", 90.0)),
        "scores": scores,
    }


def build_router_result(router_scores: dict, ground_truth_ramo: str | None = None) -> dict:
    scores = router_scores.get("scores") or {}
    ranked = sorted(
        ((ramo, payload.get("score", 0.0)) for ramo, payload in scores.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    best_ramo, confidence = ranked[0] if ranked else (UNKNOWN_RAMO, 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    threshold = float(router_scores.get("minimum_confidence", 90.0))
    tied = len([score for _ramo, score in ranked if score == confidence]) > 1
    accepted = confidence >= threshold and not tied
    classified_ramo = best_ramo if accepted else UNKNOWN_RAMO
    return {
        "classified_ramo": classified_ramo,
        "confidence": confidence,
        "threshold": threshold,
        "route_to": classified_ramo if accepted else "MANUAL_REVIEW",
        "manual_review_required": not accepted,
        "reason": "confidence_threshold_met" if accepted else "low_confidence_or_tie",
        "runner_up_score": second_score,
        "ground_truth_ramo": ground_truth_ramo,
        "ground_truth_match": None if ground_truth_ramo is None else classified_ramo == ground_truth_ramo,
    }


def route_document(
    extracted_data: dict,
    docling_data: dict | None = None,
    signals_config: dict | None = None,
    ground_truth_ramo: str | None = None,
) -> tuple[dict, dict]:
    scores = score_document_ramo(extracted_data, docling_data, signals_config)
    return scores, build_router_result(scores, ground_truth_ramo)


def save_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_router_for_artifacts(
    run_dir: Path,
    signals_path: Path = DEFAULT_RAMO_SIGNALS_PATH,
    ground_truth_ramo: str | None = None,
) -> tuple[dict, dict]:
    docling_path = run_dir / "01_docling.json"
    extracted_path = run_dir / "02_extracted.json"
    if not docling_path.exists():
        raise FileNotFoundError(f"Missing Docling artifact: {docling_path}")
    if not extracted_path.exists():
        raise FileNotFoundError(f"Missing extracted artifact: {extracted_path}")

    docling_data = json.loads(docling_path.read_text(encoding="utf-8"))
    extracted_data = json.loads(extracted_path.read_text(encoding="utf-8"))
    signals_config = load_ramo_signals(signals_path)
    scores, result = route_document(extracted_data, docling_data, signals_config, ground_truth_ramo)
    save_json(scores, run_dir / "router_scores.json")
    save_json(result, run_dir / "router_result.json")
    return scores, result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RAMO router from saved pipeline artifacts.")
    parser.add_argument("run_dir", help="Run directory containing 01_docling.json and 02_extracted.json.")
    parser.add_argument("--ramo-signals", default=str(DEFAULT_RAMO_SIGNALS_PATH))
    parser.add_argument("--ground-truth-ramo", choices=["GMM", "VIDA", "AUTOS", "DAÑOS", "UNKNOWN"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _scores, result = run_router_for_artifacts(
        Path(args.run_dir),
        signals_path=Path(args.ramo_signals),
        ground_truth_ramo=args.ground_truth_ramo,
    )
    print(f"Router result: {Path(args.run_dir) / 'router_result.json'}")
    print(f"Router scores: {Path(args.run_dir) / 'router_scores.json'}")
    print(f"Ramo: {result['classified_ramo']} ({result['confidence']})")
    print(f"Route: {result['route_to']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
