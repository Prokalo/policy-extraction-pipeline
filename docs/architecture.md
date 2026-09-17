# Architecture

This project is a PDF-to-JSON extraction pipeline for Spanish GNP medical insurance policies.

The current production entrypoint is `process_policy.py`. It orchestrates the main pipeline stages and writes both diagnostic run artifacts and a final validated JSON file.

## Current Runtime Flow

```text
source PDF
  -> Docling conversion
  -> LLM structured extraction
  -> GNP deterministic parser and repair
  -> final validation
  -> validated JSON output
```

## Main Entrypoint

`process_policy.py` is the supported end-to-end CLI and orchestrator.

Its `run_pipeline()` function performs these steps:

1. Creates output directories from `PipelineConfig`.
2. Converts the source PDF to Docling JSON, unless `--docling-json` is provided.
3. Saves the Docling result as `01_docling.json`.
4. Calls `extract_policy_from_docling()` from `pipeline/extract.py`.
5. Saves the initial extracted JSON as `02_extracted.json`.
6. Calls `process_gnp_policy()` from `pipeline/parsers/gnp.py`.
7. Calls `validate_policy()` from `pipeline/validate.py`.
8. Writes the final validated JSON to `outputs/<pdf-name>_validated.json`.

## Stage 1: Configuration

Defined in `pipeline/config.py`.

`PipelineConfig` contains runtime defaults:

- Ollama model name.
- JSON schema path.
- final output directory.
- audit run directory.
- Ollama temperature, context window, prediction size, and retry count.
- helper methods for creating run directories and final output paths.

The default model is currently `qwen3:8b`.

## Dependency Management

Runtime dependencies are declared in `pyproject.toml` for `uv`.

Use:

```bash
uv sync
uv run python process_policy.py path/to/policy.pdf
```

This does not change the extraction architecture. It only changes how the Python environment is created and how commands are run.

## Stage 2: PDF Conversion

Defined in `pipeline/extract.py`.

`convert_pdf_to_docling_dict()` uses Docling's `DocumentConverter` to convert a PDF into a dictionary representation. The converted document is saved in the run directory as `01_docling.json`.

The original PDF is still used later by the GNP parser and validator.

## Stage 3: Structured Extraction

Defined in `pipeline/extract.py`.

This stage turns Docling output into page-level evidence and asks Ollama for schema-constrained JSON.

Important responsibilities in this file:

- build page-aware evidence from Docling text and tables.
- clean Docling table/text artifacts before sending evidence to the model.
- define JSON schemas for model responses.
- call Ollama through `call_structured()`.
- validate model responses against the expected schema.
- detect insured certificate blocks.
- extract policy-level metadata from page 1.
- extract each insured certificate block.
- split and extract condition sections.

The system prompt tells the model to extract only facts supported by the supplied evidence and to return `null` when uncertain.

## Stage 4: GNP Deterministic Parser And Repair

Defined in `pipeline/parsers/gnp.py`.

This stage reopens the original PDF with PyMuPDF and performs GNP-specific deterministic parsing and cleanup. It is used because some parts of the PDF, especially coverage tables, require row and column alignment that should not rely only on the LLM.

Important responsibilities in this file:

- verify that the policy is a GNP policy.
- detect the GNP layout family.
- locate insured certificate blocks.
- rebuild insured coverage rows from PDF geometry.
- rebuild page-1 policy coverages.
- extract insured premium blocks.
- clean stale coverage warnings.
- normalize and deduplicate policy conditions.
- route some insured-specific conditions into `insured.conditions`.
- rebuild repeated certificate conditions.
- rebuild foreign-care condition data from observed tables.
- extract document sections and regulatory information.
- normalize page-1 metadata.
- write intermediate files:
  - `03_gnp_coverages.json`
  - `04_gnp_cleaned.json`
  - `05_gnp_normalized.json`

The main function in this stage is `process_gnp_policy()`.

## Stage 5: Final Validation

Defined in `pipeline/validate.py`.

This stage validates and normalizes the final data before it is written to `outputs/`.

Important responsibilities in this file:

- remove known model control artifacts from strings.
- normalize empty contact placeholders.
- normalize dates to ISO `YYYY-MM-DD` where possible.
- apply known GNP Premier foreign-care corrections.
- reconcile stale condition and currency warnings.
- correct regulatory source-page provenance when the registration number is found in the PDF.
- validate required policy and insured identifiers.
- validate premium arithmetic.
- reconcile per-insured premium totals against policy totals.
- validate `source_page` references against the PDF page count.
- detect duplicate coverage rows.
- validate the final JSON against the configured schema path, currently `schemas/gmm/policy/v3.json`.
- build `validation.summary`.

`validation.summary.sql_ready` is true only when there are no high-severity warnings.

## Legacy Standalone Stage Scripts

The current runtime pipeline is:

```text
extract.py -> parsers/gnp.py -> validate.py
```

The following files are still present in `pipeline/`, but `process_policy.py` does not import or call them:

- `extract_docling_local_v3.py`
- `repair_coverages_gnp_v2.py`
- `clean_policy_conditions_gnp_v2.py`
- `normalize_metadata_gnp_v2_1.py`
- `final_validate.py`

They represent an older, more step-by-step pipeline shape:

```text
extract_docling_local_v3.py
  -> repair_coverages_gnp_v2.py
  -> clean_policy_conditions_gnp_v2.py
  -> normalize_metadata_gnp_v2_1.py
  -> final_validate.py
```

Their responsibilities have mostly been consolidated into the current flow:

| Older standalone file | Original role | Current active location |
| --- | --- | --- |
| `extract_docling_local_v3.py` | LLM extraction from Docling evidence | `pipeline/extract.py` |
| `repair_coverages_gnp_v2.py` | Deterministic GNP coverage repair from PDF geometry | `pipeline/parsers/gnp.py` |
| `clean_policy_conditions_gnp_v2.py` | GNP condition cleanup, condition routing, regulatory/document section extraction | `pipeline/parsers/gnp.py` |
| `normalize_metadata_gnp_v2_1.py` | Page-1 deterministic metadata and premium normalization | `pipeline/parsers/gnp.py` |
| `final_validate.py` | Earlier final validator | `pipeline/validate.py` |

These older files may still be useful as implementation history, but they should not be treated as active runtime stages unless someone intentionally runs them manually.

## Output Contract

The current final JSON contract is defined in `schemas/gmm/policy/v3.json`.

The top-level fields include:

- `document`
- `policy`
- `policyholder`
- `premium_summary`
- `agent`
- `insureds`
- `policy_conditions`
- `policy_coverages`
- `regulatory`
- `document_sections`
- `condition_heading_audit`
- `validation`

Other schema paths exist for future branches, but they are placeholders unless implementation and tests are added:

- `schemas/autos/policy/v1.json`
- `schemas/daños/policy/v1.json`
- `schemas/vida/policy/v1.json`

## Runtime Artifacts

Each run creates a timestamped folder under:

```text
audit_logs/runs/<pdf-name>_<YYYYMMDD_HHMMSS>/
```

The normal run artifacts are:

```text
01_docling.json
02_extracted.json
03_gnp_coverages.json
04_gnp_cleaned.json
05_gnp_normalized.json
```

If an Ollama call fails validation, debug files named `debug_<tag>_attempt_<n>.txt` may also be written to the run directory.

The final output is written to:

```text
outputs/<pdf-name>_validated.json
```

## Important Boundary

The LLM is not treated as the final authority. The code uses the LLM for constrained extraction from evidence, then applies deterministic GNP-specific parsing and validation over the original PDF.

## Architecture Notes From Graphify

`Graphify-Out/GRAPH_REPORT.md` is a generated code graph report. It is not part of the runtime pipeline.

The report identifies these central nodes:

- `process_gnp_policy()`
- `PipelineConfig`
- `extract_policy_from_docling()`
- `validate_policy()`
- `clean_policy_conditions()`

It also reports no detected import cycles.

The report suggests that `extract.py`, `validate.py`, and `repair_coverages_gnp_v2.py` are large or low-cohesion areas. That is a refactoring signal, not a runtime requirement.
