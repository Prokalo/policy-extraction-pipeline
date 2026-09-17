# Supported Policies

This document describes what the current code supports based only on the repository code and README.

## Current Scope

The current pipeline is GNP-specific.

The code is designed around Spanish-language GNP medical insurance policy PDFs and contains GNP-specific:

- headings.
- coverage labels.
- condition names.
- plan rules.
- PDF geometry assumptions.
- metadata labels.
- regulatory extraction patterns.

The main GNP implementation is `pipeline/parsers/gnp.py`.

## Currently Known Working Policy Family

The README example uses:

```text
GMM Línea Azul
```

The extraction prompt in `pipeline/extract.py` also explicitly references `Línea Azul` as a product line.

Based on the current code and README, `GMM Línea Azul` is the clearest currently supported policy family.

## Layouts Mentioned Or Encoded In The Code

The current GNP parser has layout detection for:

- `gnp_premier`
- `gnp_flexible`
- `gnp_unknown`

Detection is based on the extracted insurer and plan text, with a fallback search on page 1.

The older standalone coverage repair script says it supports the two GNP layouts tested so far:

- Flexible / Ámbar style certificate.
- Premier style certificate.

Because that statement is in an older standalone script, this document treats it as implementation history unless verified through the current end-to-end pipeline.

## Current Hard Stop

`pipeline/parsers/gnp.py` raises an error when the extracted insurer does not look like Grupo Nacional Provincial.

That means the current deterministic parser does not support non-GNP insurers.

## What Is Not Supported Yet

The current code does not prove support for all GNP Seguros policy products.

The parser currently has hard-coded coverage names and PDF layout assumptions. That means other GNP products may fail or produce high-severity validation warnings if their layouts, labels, tables, or condition sections differ from the tested patterns.

Do not treat this as an all-GNP extraction pipeline yet.

## Future Goal

The desired direction is to make this a more dynamic GNP Seguros extraction pipeline that can support more or all GNP policy products.

That likely requires adding explicit support for additional GNP product layouts and validating each one with real PDFs and tests.

## Evidence Needed Before Marking A Policy As Supported

A policy product should only be added to the supported list after the repository has evidence such as:

- at least one real or representative PDF fixture for that product.
- a successful full run through `process_policy.py`.
- no high-severity validation warnings, or documented accepted exceptions.
- tests or fixture checks covering the product's layout.
- confirmed coverage, premium, condition, regulatory, and source-page extraction behavior.

## Current Supported List

| Insurer | Product / policy family | Status | Notes |
| --- | --- | --- | --- |
| GNP Seguros / Grupo Nacional Provincial | GMM Línea Azul | Supported by current project focus | This is the clearest supported family based on README usage and code references. |

## Candidate / Partially Encoded Layouts

These are present in the code but should not be treated as fully supported without fixture verification:

| Insurer | Layout / plan family | Status | Evidence in code |
| --- | --- | --- | --- |
| GNP Seguros / Grupo Nacional Provincial | Premier | Candidate / partially encoded | `detect_layout()` can return `gnp_premier`; foreign-care corrections reference Premier 100/200/300/400. |
| GNP Seguros / Grupo Nacional Provincial | Flexible / Ámbar | Candidate / partially encoded | `detect_layout()` can return `gnp_flexible`; older standalone coverage repair script says Flexible / Ámbar style was tested. |

## Notes For Expanding Support

Before adding a new GNP product, inspect whether these areas need changes:

- `pipeline/parsers/gnp.py`
- coverage label matching.
- page-1 coverage extraction.
- insured certificate block detection.
- condition heading detection and cleanup.
- foreign-care matrix handling.
- metadata normalization.
- `schemas/gmm/policy/v3.json`, if the GMM output contract needs new fields.
- tests under `tests/`.

Keep support claims tied to actual fixtures and successful validation results.

## Expansion Approach

The current rigidity mostly comes from `pipeline/parsers/gnp.py`. It contains hard-coded GNP coverage names, page-1 metadata patterns, condition heading assumptions, foreign-care logic, and PDF geometry rules.

To make support broader, avoid adding unrelated product-specific branches directly into the existing functions without tests. A safer direction is to separate the flow into:

- product or layout detection.
- product/layout-specific parser rules.
- shared extraction helpers.
- shared final validation.

For example, a future structure could look like:

```text
pipeline/
  parsers/
    gnp/
      router.py
      base.py
      linea_azul.py
      premier.py
      flexible_ambar.py
      shared.py
```

This structure does not exist today. It is a possible refactoring direction for supporting more GNP products without turning `pipeline/parsers/gnp.py` into one very large file.

Each new policy family should be added with:

- a representative PDF fixture or saved Docling fixture.
- a detection rule.
- parser behavior for coverages, premiums, conditions, regulatory data, and metadata.
- validation expectations.
- tests proving the product can complete the full pipeline.
