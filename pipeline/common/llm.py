from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from ollama import chat

from pipeline.common.text import keytext, norm
from pipeline.config import PipelineConfig


COMMON = "COMMON"
NOTES_MAX_LENGTH = 300

SYSTEM_PROMPT = """You are a strict Spanish insurance-policy extraction engine.
Extract only facts explicitly supported by the supplied Docling evidence. Never invent; if uncertain return null.
Preserve names, RFC/tax IDs, policy numbers, customer codes, Spanish plan names and coverage names exactly.
Normalize pesos -> MXN and dls/dólares norteamericanos -> USD only in normalized currency fields.
Never borrow sum insured, deductible, coinsurance, or service cost from an adjacent row. Values must belong to the same logical coverage row.
If row association is uncertain, return null and add a warning. Preserve every row of rule tables.
The notes field is only for meaningful facts not represented by another structured field. Never put a structured value in notes a second time.
Never copy tables, Markdown table syntax, cell separators, empty cells, page formatting, or repeated whitespace into notes.
Use null when there is no additional fact for notes, and never exceed 300 characters. Return JSON matching the supplied schema."""


def warning_schema():
    return {"type": "object", "properties": {
        "field": {"type": ["string", "null"]}, "issue": {"type": ["string", "null"]},
        "severity": {"type": ["string", "null"], "enum": ["low", "medium", "high", None]},
        "source_page": {"type": ["integer", "null"]}},
        "required": ["field", "issue", "severity", "source_page"]}


def money_schema():
    return {"type": "object", "properties": {
        "raw_value": {"type": ["string", "null"]}, "amount": {"type": ["number", "null"]},
        "currency": {"type": ["string", "null"]}},
        "required": ["raw_value", "amount", "currency"]}


def _scalar_strings(value) -> list[str]:
    if isinstance(value, dict):
        return [item for key, child in value.items() if key != "notes" for item in _scalar_strings(child)]
    if isinstance(value, list):
        return [item for child in value for item in _scalar_strings(child)]
    if isinstance(value, str):
        return [norm(value)] if norm(value) else []
    return []


def validate_notes(value, path: str = "$") -> list[str]:
    """Return errors for unsafe or duplicative notes anywhere in a model object."""
    errors = []
    if isinstance(value, dict):
        note = value.get("notes")
        if isinstance(note, str):
            note_path = f"{path}.notes"
            if not note.strip():
                errors.append(f"{note_path} must be null when there is no additional information")
            if len(note) > NOTES_MAX_LENGTH:
                errors.append(f"{note_path} exceeds {NOTES_MAX_LENGTH} characters")
            if "|" in note or re.search(r"(?:^|\n)\s*:?-{3,}:?\s*(?:\||$)", note):
                errors.append(f"{note_path} contains table or separator artifacts")
            if re.search(r"[\r\n\t]|\s{2,}", note):
                errors.append(f"{note_path} contains page formatting or repeated whitespace")

            note_key = keytext(note)
            for structured_value in _scalar_strings(value):
                structured_key = keytext(structured_value)
                if len(structured_key) >= 8 and structured_key in note_key:
                    errors.append(
                        f"{note_path} duplicates structured value {structured_value!r}"
                    )
                    break
        for key, child in value.items():
            if key != "notes":
                errors.extend(validate_notes(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            errors.extend(validate_notes(child, f"{path}[{index}]"))
    return errors


def validate_structured_response(value: object, schema: dict) -> None:
    errors = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(value)
    ]
    errors.extend(validate_notes(value))
    if errors:
        raise ValueError("; ".join(errors[:10]))


def call_structured(model: str, schema: dict, prompt: str, config: PipelineConfig, debug_dir: Path, tag: str = "call") -> dict:
    last = None
    retry_issue = None
    for attempt in range(1, config.ollama_retries + 2):
        content = ""
        try:
            attempt_prompt = prompt
            if retry_issue:
                attempt_prompt += (
                    "\n\nYour previous response for this same object failed validation. "
                    f"Correct only this object and return it again. Validation error: {retry_issue}"
                )
                if ".notes" in retry_issue:
                    attempt_prompt += (
                        "\nFor every notes field named in that error, set notes to null. "
                        "Do not move the duplicated text into another notes field or explain the correction in warnings."
                    )
            response = chat(
                model=model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": attempt_prompt}],
                format=schema,
                think=False,
                options={
                    "temperature": config.ollama_temperature,
                    "num_predict": config.ollama_num_predict,
                    "num_ctx": config.ollama_num_ctx,
                },
            )
            content = response.message.content
            parsed = json.loads(content)
            validate_structured_response(parsed, schema)
            return parsed
        except Exception as exc:
            last = exc
            retry_issue = str(exc)
            (debug_dir / f"debug_{tag}_attempt_{attempt}.txt").write_text(content, encoding="utf-8")
            if attempt <= config.ollama_retries:
                continue
    raise RuntimeError(f"{tag} failed after retries: {last}")
