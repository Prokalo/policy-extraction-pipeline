#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.bootstrap import activate_local_venv

activate_local_venv()

from pipeline.config import DEFAULT_CONFIG, PipelineConfig
from pipeline.extract import convert_pdf_to_docling_dict, extract_policy_from_docling, load_json, save_json
from pipeline.parsers.gnp import process_gnp_policy
from pipeline.validate import validate_policy


class PipelineFailure(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="Path to source policy PDF.")
    parser.add_argument("--model", default=DEFAULT_CONFIG.model_name)
    parser.add_argument("--schema", default=str(DEFAULT_CONFIG.schema_path))
    parser.add_argument("--docling-json", help="Optional precomputed Docling JSON to reuse.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(model_name=args.model, schema_path=Path(args.schema))


def high_severity_issues(data: dict) -> list[str]:
    issues = []
    for warning in data.get("validation", {}).get("warnings", []):
        if warning.get("severity") == "high":
            field = warning.get("field") or "unknown"
            issue = warning.get("issue") or "Unknown validation issue."
            issues.append(f"{field}: {issue}")
    return issues


def run_pipeline(pdf_path: Path, config: PipelineConfig, docling_json_override: str | None = None) -> tuple[dict, Path, dict]:
    config.ensure_dirs()
    run_dir = config.make_run_dir(pdf_path)

    if docling_json_override:
        docling_data = load_json(Path(docling_json_override))
    else:
        docling_data = convert_pdf_to_docling_dict(pdf_path)
    docling_path = run_dir / "01_docling.json"
    save_json(docling_data, docling_path)

    extracted_data, extraction_report = extract_policy_from_docling(docling_data, config, run_dir)
    extracted_path = run_dir / "02_extracted.json"
    save_json(extracted_data, extracted_path)

    parsed_data, gnp_report = process_gnp_policy(extracted_data, pdf_path, run_dir)
    validated_data, validation_report = validate_policy(parsed_data, pdf_path, config.schema_path)

    output_path = config.final_output_path(pdf_path)
    save_json(validated_data, output_path)

    report = {
        "run_dir": str(run_dir),
        "docling_path": str(docling_path),
        "extracted_path": str(extracted_path),
        "output_path": str(output_path),
        "extraction": extraction_report,
        "gnp": gnp_report,
        "validation": validation_report,
    }
    return validated_data, output_path, report


def main() -> int:
    args = parse_args()
    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    config = build_config(args)
    try:
        data, output_path, report = run_pipeline(pdf_path, config, args.docling_json)
    except Exception as exc:
        print(f"ERROR: pipeline failed: {exc}", file=sys.stderr)
        return 2

    summary = data.get("validation", {}).get("summary", {})
    high_issues = high_severity_issues(data)

    print(f"Run folder: {report['run_dir']}")
    print(f"Docling JSON: {report['docling_path']}")
    print(f"Extracted JSON: {report['extracted_path']}")
    print(f"Validated JSON: {output_path}")
    print(f"GNP layout: {report['gnp']['layout']}")
    print(f"Insureds: {report['extraction']['insured_count']}")
    for page_report in report["extraction"].get("condition_pages", []):
        print(
            "Condition page"
            f" {page_report['page']}: extracted={page_report['extracted_headings']} failed={page_report['failed_headings']}"
        )
    print(f"Policy conditions: {report['gnp']['final_policy_conditions']}")
    print(f"Insured condition counts: {report['gnp'].get('final_insured_condition_counts')}")
    print(f"Schema errors: {report['validation']['schema_error_count']}")
    print(f"High severity warnings: {summary.get('high_severity_count')}")
    print(f"SQL ready: {summary.get('sql_ready')}")

    if summary.get("high_severity_count", 0) > 0 or not summary.get("sql_ready", False):
        print("VALIDATION FAILED:", file=sys.stderr)
        for issue in high_issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print("VALIDATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
