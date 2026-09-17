# Debugging And Expanding Policy Support

This document explains how to approach failures in the current pipeline and how to think about expanding from one working policy family to multiple GNP policy families.

## Current Mental Model

The active pipeline is:

```text
process_policy.py
  -> pipeline/extract.py
  -> pipeline/parsers/gnp.py
  -> pipeline/validate.py
```

In simpler terms:

```text
extract -> parse/repair -> validate
```

## Where To Look When Something Breaks

Each run writes intermediate files under:

```text
audit_logs/runs/<pdf-name>_<YYYYMMDD_HHMMSS>/
```

Use those files to locate the stage where the data first becomes wrong.

### `01_docling.json`

This is the raw Docling conversion.

Check this when:

- text is missing from the PDF.
- table structure is missing or strange.
- page numbers are wrong.
- evidence is not available to the extractor.

If the information is missing here, later stages may not be able to recover it unless they read the original PDF directly with PyMuPDF.

### `02_extracted.json`

This is the first structured extraction from `pipeline/extract.py`.

Check this when:

- policy metadata is wrong.
- insureds are missing.
- condition sections were missed.
- the LLM placed data in the wrong fields.
- warnings came from model extraction.

If the value is already wrong here but the evidence exists in `01_docling.json`, the issue is probably in evidence construction, prompts, schemas, or model response validation inside `pipeline/extract.py`.

### `03_gnp_coverages.json`

This is written by `pipeline/parsers/gnp.py` after deterministic coverage repair.

Check this when:

- coverages are missing.
- deductible, coinsurance, service cost, or sum insured values are attached to the wrong row.
- a new policy layout has different columns or coverage labels.
- policy-level and insured-level coverages are mixed up.

Failures here usually point to GNP-specific PDF geometry or coverage label matching.

### `04_gnp_cleaned.json`

This is written after condition cleanup and document/regulatory extraction.

Check this when:

- conditions are duplicated.
- conditions are attached to the wrong scope.
- insured-specific conditions should move into `insured.conditions` but do not.
- legal/admin document sections are missing.
- regulatory data is missing.

Failures here usually point to condition normalization, condition routing, repeated condition rebuilding, or regulatory extraction.

### `05_gnp_normalized.json`

This is written after deterministic page-1 metadata normalization.

Check this when:

- policy dates are wrong.
- term days are wrong.
- premium summary fields are wrong.
- payment method or payment channel is wrong.
- agent code is wrong.

Failures here usually point to page-1 regex patterns in `pipeline/parsers/gnp.py`.

### Final `outputs/<pdf-name>_validated.json`

This is the final output after `pipeline/validate.py`.

Check this when:

- the output is not SQL-ready.
- high-severity warnings remain.
- schema validation errors exist.
- premium reconciliation fails.
- source-page provenance is invalid.

Validation does not mean extraction was correct. It means the final object passed schema and business-readiness checks well enough to be marked SQL-ready.

## How To Diagnose A New Policy Failure

When a new GNP policy partially breaks, use this process:

1. Run the full pipeline and note the run directory.
2. Open `outputs/<pdf-name>_validated.json` and inspect `validation.summary`.
3. Inspect all high-severity warnings first.
4. Compare `02_extracted.json`, `03_gnp_coverages.json`, `04_gnp_cleaned.json`, and `05_gnp_normalized.json`.
5. Identify the first file where the data becomes wrong.
6. Fix the module that produced that file.
7. Add a test or fixture check before treating the policy as supported.

## Common Failure Areas

### Extraction Failure

Likely module:

```text
pipeline/extract.py
```

Typical symptoms:

- insured blocks are not discovered.
- page evidence is incomplete.
- model output fails schema validation.
- condition headings are missed.

### Coverage Parser Failure

Likely module:

```text
pipeline/parsers/gnp.py
```

Typical symptoms:

- a coverage name is not recognized.
- table columns changed.
- row spans are different.
- values from one row appear in another row.

### Metadata Normalization Failure

Likely module:

```text
pipeline/parsers/gnp.py
```

Typical symptoms:

- dates, premiums, currency, payment method, or agent code are wrong after `05_gnp_normalized.json`.

### Validation Failure

Likely module:

```text
pipeline/validate.py
```

Typical symptoms:

- final data is structurally present but not SQL-ready.
- required identifiers are missing.
- source pages are invalid.
- premium arithmetic does not reconcile.
- schema errors are added to `validation.warnings`.

## Current Rigidity

The current parser is rigid because one file, `pipeline/parsers/gnp.py`, contains many GNP-specific assumptions:

- insurer detection.
- layout detection.
- coverage names.
- coverage table geometry.
- page-1 metadata regexes.
- condition headings and aliases.
- foreign-care table logic.
- regulatory extraction.

This works for the tested structure, but a new GNP product can break if any of those assumptions change.

## Direction For Dynamic Multi-Policy Support

The goal should not be one completely generic parser that guesses everything. Insurance PDFs are too layout- and product-specific for that to be reliable.

A safer target is a router plus product/layout-specific parsers:

```text
common extraction
  -> detect insurer/product/layout
  -> choose parser implementation
  -> run product-specific repair/normalization
  -> run shared validation
```

Possible future structure:

```text
pipeline/
  parsers/
    gnp/
      router.py
      shared.py
      linea_azul.py
      premier.py
      flexible_ambar.py
```

This structure does not exist today. It is a refactoring direction.

## Suggested Migration Path

### Step 1: Keep The Current Pipeline Working

Do not start by rewriting everything.

First, preserve the current working GMM Línea Azul behavior with tests and fixture outputs. That gives you a baseline.

### Step 2: Add A Parser Interface

Introduce a small contract for parser modules.

Conceptually, each parser needs to answer:

- can this parser handle this policy?
- how should coverages be parsed?
- how should metadata be normalized?
- how should conditions be cleaned?
- what report data should be returned?

### Step 3: Add A GNP Router

Move layout/product detection into a router.

The router should inspect extracted metadata and/or PDF text, then choose the right parser.

Current detection clues already exist in `detect_layout()`:

- insurer contains Grupo Nacional Provincial.
- plan text contains Premier.
- plan text contains Flexible or Ámbar.
- page 1 contains Premier.

### Step 4: Split Shared Helpers From Product Rules

Move reusable helpers into shared modules before adding more policy families.

Examples:

- `norm`
- `deaccent`
- `keytext`
- money parsing.
- coinsurance parsing.
- warning helpers.
- common PDF line grouping.

Then keep product/layout-specific rules separate.

### Step 5: Add One New Policy Family At A Time

For each new GNP product:

1. Add a fixture or saved run artifact.
2. Run the current pipeline.
3. Find the first failed stage.
4. Add detection for the product/layout.
5. Add parser rules only for the broken areas.
6. Add tests.
7. Update `docs/supported-policies.md`.

### Step 6: Keep Validation Shared Where Possible

`pipeline/validate.py` should stay mostly shared if the final JSON contract remains the same.

Only split validation if different policy families genuinely require different readiness rules.

## Rule For Marking A Policy As Supported

A policy family should not be called supported just because the pipeline runs.

It should be marked supported only when:

- the full pipeline completes.
- the final output has no unexpected high-severity warnings.
- critical fields are verified.
- source pages are correct.
- coverages and premiums reconcile.
- there is a test or fixture proving the behavior.

## Practical Rule

When a new policy breaks, do not ask "how do I make the whole pipeline dynamic?"

Ask:

```text
Which stage first becomes wrong?
What assumption did that stage make?
Should that assumption be shared, or specific to one GNP product/layout?
```

That question keeps the code from turning into a pile of special cases.
