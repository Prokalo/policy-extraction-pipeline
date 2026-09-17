# Folder Structure

This document describes the folders and files that exist in the current codebase.

## Root Files

### `process_policy.py`

The supported end-to-end CLI and orchestrator.

Use this file to run the full pipeline:

```bash
uv run python process_policy.py path/to/policy.pdf
```

It imports and coordinates:

- `pipeline.config`
- `pipeline.extract`
- `pipeline.parsers.gnp`
- `pipeline.validate`

### `schemas/`

Versioned JSON Schema contracts.

Current schema files:

- `schemas/gmm/policy/v3.json`: current GMM policy output contract.
- `schemas/autos/policy/v1.json`: placeholder; autos extraction is not implemented yet.
- `schemas/daños/policy/v1.json`: placeholder; daños extraction is not implemented yet.
- `schemas/vida/policy/v1.json`: placeholder; vida extraction is not implemented yet.

The final validator checks pipeline output against the configured schema path.

### `pyproject.toml`

Defines the Python project metadata and runtime dependencies for `uv`.

Current runtime dependencies:

- `docling`
- `jsonschema`
- `ollama`
- `pymupdf`

### `README.md`

The current high-level project documentation. It includes usage, outputs, configuration, validation behavior, repository layout, tests, and troubleshooting.

## `pipeline/`

The main Python package for the extraction pipeline.

On this machine, `Pipeline/` and `pipeline/` resolve to the same directory because the filesystem is case-insensitive. The Python imports use lowercase `pipeline`.

### `pipeline/__init__.py`

Exports `DEFAULT_CONFIG` and `PipelineConfig`.

### `pipeline/bootstrap.py`

Adds the repository's local `.venv` site-packages path to `sys.path` when the virtual environment exists.

### `pipeline/config.py`

Defines `PipelineConfig` and `DEFAULT_CONFIG`.

This is where default paths and Ollama settings live.

### `pipeline/extract.py`

Current extraction stage.

Responsibilities:

- convert PDFs to Docling JSON.
- build page-level evidence from Docling output.
- define schemas for structured model responses.
- call Ollama through `call_structured()`.
- validate model responses.
- detect insured certificate blocks.
- extract policy metadata, insured blocks, and condition sections.

### `pipeline/parsers/`

Parser package for insurer-specific logic.

### `pipeline/parsers/__init__.py`

Exports `process_gnp_policy`.

### `pipeline/parsers/gnp.py`

Current consolidated deterministic GNP parser.

Responsibilities:

- detect GNP layout.
- rebuild coverages from PDF geometry.
- extract policy-level coverages.
- extract insured premium blocks.
- clean, normalize, rebuild, and deduplicate conditions.
- extract document sections and regulatory data.
- normalize page-1 metadata.
- save intermediate GNP-stage JSON artifacts.

This file is currently GNP-specific.

### `pipeline/validate.py`

Current final validator.

Responsibilities:

- normalize dates and contact fields.
- sanitize known model artifacts.
- validate required identifiers.
- validate premium arithmetic.
- validate source-page provenance.
- detect duplicate coverages.
- validate against the configured schema path, currently `schemas/gmm/policy/v3.json`.
- build the final validation summary.

### Older Standalone Stage Scripts

These files are present but are not called by the current `process_policy.py` end-to-end flow:

- `pipeline/extract_docling_local_v3.py`
- `pipeline/repair_coverages_gnp_v2.py`
- `pipeline/clean_policy_conditions_gnp_v2.py`
- `pipeline/normalize_metadata_gnp_v2_1.py`
- `pipeline/final_validate.py`

They appear to be earlier standalone versions of functionality that now lives in the current flow, especially inside `pipeline/parsers/gnp.py` and `pipeline/validate.py`.

#### `pipeline/extract_docling_local_v3.py`

Older standalone extraction script.

It performed the same broad kind of work now handled by `pipeline/extract.py`: build evidence from Docling output, call Ollama with JSON schemas, and produce an initial structured insurance JSON.

#### `pipeline/repair_coverages_gnp_v2.py`

Older standalone GNP coverage repair script.

It rebuilt policy and insured coverage rows from PDF word geometry. Its header says it supported the two GNP layouts tested at the time:

- Flexible / Ámbar style certificate.
- Premier style certificate.

The active consolidated version of this responsibility now lives in `pipeline/parsers/gnp.py`.

#### `pipeline/clean_policy_conditions_gnp_v2.py`

Older standalone condition and document cleanup script.

Its header lists these responsibilities:

- keep true policy rules in `policy_conditions`.
- route insured waiting-period history to `insured.conditions`.
- canonicalize and merge preexistence tables.
- correct the Premier 400 footnote so it belongs to foreign-care coverage.
- extract legal/admin text into `document_sections`.
- extract regulatory registration directly from the PDF.

The active consolidated version of this responsibility now lives in `pipeline/parsers/gnp.py`.

#### `pipeline/normalize_metadata_gnp_v2_1.py`

Older standalone metadata normalization script.

It parsed page 1 directly for values such as policy dates, term days, payment method, payment channel, currency, premium amounts, tax rate, and agent code.

The active consolidated version of this responsibility now lives in `pipeline/parsers/gnp.py`.

#### `pipeline/final_validate.py`

Older standalone final validator.

The active final validator is `pipeline/validate.py`.

## `tests/`

Unit and regression tests.

Current test files:

- `tests/test_condition_sections.py`
- `tests/test_extraction_boundaries.py`
- `tests/test_gnp_foreign_care.py`

The tests cover condition section extraction, extraction boundaries, condition cleanup/routing, GNP foreign-care behavior, and related validation behavior.

Run tests with:

```bash
uv run pytest
```

The README notes that some fixture-style tests may depend on local PDFs or timestamped artifacts that may not exist in every checkout.

## `outputs/`

Final validated JSON outputs are written here.

The output filename pattern is:

```text
<pdf-name>_validated.json
```

Re-running the same PDF overwrites the final output file for that PDF name.

## `audit_logs/`

Diagnostic and intermediate artifacts.

The current full pipeline writes timestamped run folders under:

```text
audit_logs/runs/
```

Each run folder can contain:

- `01_docling.json`
- `02_extracted.json`
- `03_gnp_coverages.json`
- `04_gnp_cleaned.json`
- `05_gnp_normalized.json`
- `debug_*.txt` files when model calls fail validation.

## `test_policies/`

Sample policy PDFs, when present locally.

The README references this folder for example policy inputs and notes that some expected fixture PDFs may be absent in the current checkout.

## `Graphify-Out/` / `graphify-out/`

Generated code graph artifacts.

On this machine, `Graphify-Out/` and `graphify-out/` resolve to the same directory because the filesystem is case-insensitive.

Important files:

- `GRAPH_REPORT.md`: human-readable graph report.
- `graph.html`: visual graph output.
- `graph.json`: graph data.
- `manifest.json`: Graphify metadata.
- dated subfolders with previous graph reports.

This folder is for analysis and navigation. It is not used by the runtime extraction pipeline.

## `Archive/`

Historical or superseded scripts and outputs.

This folder was not inspected as part of this documentation pass.

## `.venv/`

Local Python virtual environment, when present.

`uv sync` creates and manages this environment. `process_policy.py` also calls `activate_local_venv()` so the repository can discover packages installed in this local environment, but using `uv run` is recommended.
