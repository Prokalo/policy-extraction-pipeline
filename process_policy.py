#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pipeline.bootstrap import activate_local_venv

activate_local_venv()

from pipeline.config import DEFAULT_CONFIG, PipelineConfig
from pipeline.branches.gmm.extract import convert_pdf_to_docling_dict, extract_policy_from_docling, load_json, save_json
from pipeline.parsers.gnp import process_gnp_policy
from pipeline.router import load_ramo_signals, route_document
from pipeline.branches.gmm.validate import validate_policy


class PipelineFailure(RuntimeError):
    pass


IMPLEMENTED_RAMO_BRANCHES = {"GMM"}
PLANNED_RAMO_BRANCHES = {"VIDA", "AUTOS", "DAÑOS"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="Path to source policy PDF.")
    parser.add_argument("--model", default=DEFAULT_CONFIG.model_name)
    parser.add_argument("--schema", help="Override the routed branch schema path.")
    parser.add_argument("--ramo-signals", default=str(DEFAULT_CONFIG.ramo_signals_path))
    parser.add_argument("--docling-json", help="Optional precomputed Docling JSON to reuse.")
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> PipelineConfig:
    config_kwargs = {
        "model_name": args.model,
        "ramo_signals_path": Path(args.ramo_signals),
    }
    if args.schema:
        config_kwargs["schema_path"] = Path(args.schema)
        config_kwargs["schema_path_overrides_all_branches"] = True
    return PipelineConfig(**config_kwargs)


def high_severity_issues(data: dict) -> list[str]:
    issues = []
    for warning in data.get("validation", {}).get("warnings", []):
        if warning.get("severity") == "high":
            field = warning.get("field") or "unknown"
            issue = warning.get("issue") or "Unknown validation issue."
            issues.append(f"{field}: {issue}")
    return issues


def dispatch_ramo_branch(
    ramo: str,
    docling_data: dict,
    config: PipelineConfig,
    pdf_path: Path,
    run_dir: Path,
) -> tuple[dict, dict, dict, Path | None]:
    if ramo == "GMM":
        print("Correclty classified as GMM, moving on to LLM extraction/parsing")
        extracted_data, extraction_report = extract_policy_from_docling(docling_data, config, run_dir)
        extracted_path = run_dir / "02_extracted.json"
        save_json(extracted_data, extracted_path)
        parsed_data, branch_report = process_gnp_policy(extracted_data, pdf_path, run_dir)
        branch_report = {"ramo": ramo, "branch": "gnp_gmm", "status": "completed", **branch_report}
        return parsed_data, branch_report, extraction_report, extracted_path
    if ramo in PLANNED_RAMO_BRANCHES:
        print(f"Correclty classified as {ramo}, moving on to LLM extraction/parsing")
        raise PipelineFailure(f"Document routed to {ramo}, but the {ramo} extraction branch is not implemented yet.")
    print("failed to passed classifer")
    raise PipelineFailure(f"Document routed to unsupported branch: {ramo}.")


def run_pipeline(pdf_path: Path, config: PipelineConfig, docling_json_override: str | None = None) -> tuple[dict, Path, dict]:
    config.ensure_dirs()
    run_dir = config.make_run_dir(pdf_path)

    if docling_json_override:
        docling_data = load_json(Path(docling_json_override))
    else:
        docling_data = convert_pdf_to_docling_dict(pdf_path)
    docling_path = run_dir / "01_docling.json"
    save_json(docling_data, docling_path)

    router_config = load_ramo_signals(config.ramo_signals_path)
    router_scores, router_result = route_document(docling_data, router_config)
    router_scores_path = run_dir / "router_scores.json"
    router_result_path = run_dir / "router_result.json"
    save_json(router_scores, router_scores_path)
    save_json(router_result, router_result_path)
    branch_dispatch = {
        "classified_ramo": router_result["classified_ramo"],
        "confidence": router_result["confidence"],
        "route_to": router_result["route_to"],
        "implemented_branches": sorted(IMPLEMENTED_RAMO_BRANCHES),
        "planned_branches": sorted(PLANNED_RAMO_BRANCHES),
        "status": "pending",
    }
    branch_dispatch_path = run_dir / "branch_dispatch.json"
    if router_result["route_to"] == "MANUAL_REVIEW":
        branch_dispatch["status"] = "manual_review"
        save_json(branch_dispatch, branch_dispatch_path)
        raise PipelineFailure(
            "Document routed to manual review: "
            f"ramo={router_result['classified_ramo']} confidence={router_result['confidence']}"
        )

    try:
        parsed_data, branch_report, extraction_report, extracted_path = dispatch_ramo_branch(
            router_result["route_to"],
            docling_data,
            config,
            pdf_path,
            run_dir,
        )
    except PipelineFailure as exc:
        branch_dispatch["status"] = "not_implemented_or_unsupported"
        branch_dispatch["error"] = str(exc)
        save_json(branch_dispatch, branch_dispatch_path)
        raise
    branch_dispatch["status"] = "completed"
    branch_dispatch["branch"] = branch_report.get("branch")
    save_json(branch_dispatch, branch_dispatch_path)

    validation_schema_path = config.schema_path_for(router_result["route_to"])
    validated_data, validation_report = validate_policy(parsed_data, pdf_path, validation_schema_path)

    output_path = config.final_output_path(pdf_path, router_result["route_to"])
    save_json(validated_data, output_path)

    report = {
        "run_dir": str(run_dir),
        "docling_path": str(docling_path),
        "extracted_path": str(extracted_path) if extracted_path else None,
        "router_scores_path": str(router_scores_path),
        "router_result_path": str(router_result_path),
        "branch_dispatch_path": str(branch_dispatch_path),
        "validation_schema_path": str(validation_schema_path),
        "output_path": str(output_path),
        "extraction": extraction_report,
        "router": router_result,
        "branch": branch_report,
        "gnp": branch_report,
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
    print(f"Router scores: {report['router_scores_path']}")
    print(f"Router result: {report['router_result_path']}")
    print(f"Ramo: {report['router']['classified_ramo']} ({report['router']['confidence']})")
    print(f"Branch dispatch: {report['branch_dispatch_path']}")
    print(f"Validated JSON: {output_path}")
    print(f"Branch: {report['branch']['branch']}")
    print(f"GNP layout: {report['branch']['layout']}")
    print(f"Insureds: {report['extraction']['insured_count']}")
    for page_report in report["extraction"].get("condition_pages", []):
        print(
            "Condition page"
            f" {page_report['page']}: extracted={page_report['extracted_headings']} failed={page_report['failed_headings']}"
        )
    print(f"Policy conditions: {report['branch']['final_policy_conditions']}")
    print(f"Insured condition counts: {report['branch'].get('final_insured_condition_counts')}")
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
