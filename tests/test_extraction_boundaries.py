from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.config import PipelineConfig
from pipeline.extract import (
    build_page_items,
    call_structured,
    clean_evidence_text,
    validate_structured_response,
)


class EvidenceCleanupTests(unittest.TestCase):
    def test_table_cleanup_drops_empty_rows_and_cells_but_keeps_column_positions(self):
        doc = {
            "texts": [],
            "tables": [
                {
                    "prov": [{"page_no": 2, "bbox": {"t": 500, "b": 400}}],
                    "data": {
                        "grid": [
                            [{"text": ""}, {"text": ""}, {"text": ""}],
                            [{"text": "Cobertura"}, {"text": ""}, {"text": "500.00 por servicio"}],
                        ]
                    },
                }
            ],
        }

        items = build_page_items(doc)[2]

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["text"], "C1: Cobertura ; C3: 500.00 por servicio")
        self.assertNotIn("|", items[0]["text"])

    def test_text_cleanup_removes_blank_markdown_structures(self):
        raw = "\n|  |  |  |\n| --- | --- |\n  Dato   real  \n\nDato   real\n"
        self.assertEqual(clean_evidence_text(raw), "Dato real")


class StructuredResponseValidationTests(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "properties": {
            "service_cost": {
                "type": "object",
                "properties": {"raw_value": {"type": ["string", "null"]}},
                "required": ["raw_value"],
            },
            "notes": {"type": ["string", "null"], "maxLength": 300},
        },
        "required": ["service_cost", "notes"],
    }

    def test_rejects_schema_invalid_response(self):
        with self.assertRaisesRegex(ValueError, "required property"):
            validate_structured_response({"notes": None}, self.SCHEMA)

    def test_rejects_table_artifacts_and_duplicate_structured_values_in_notes(self):
        bad_values = [
            {
                "service_cost": {"raw_value": "500.00 por servicio"},
                "notes": "Membresía |  | 500.00 por servicio",
            },
            {
                "service_cost": {"raw_value": "500.00 por servicio"},
                "notes": "Costo adicional: 500.00 por servicio",
            },
        ]
        for value in bad_values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_structured_response(value, self.SCHEMA)

    def test_accepts_short_meaningful_nonduplicative_note_or_null(self):
        validate_structured_response(
            {"service_cost": {"raw_value": "500.00 por servicio"}, "notes": "Requiere autorización previa."},
            self.SCHEMA,
        )
        validate_structured_response(
            {"service_cost": {"raw_value": "500.00 por servicio"}, "notes": None},
            self.SCHEMA,
        )

    def test_failed_attempt_is_saved_and_only_same_call_is_retried(self):
        invalid_raw = json.dumps(
            {"service_cost": {"raw_value": "500.00 por servicio"}, "notes": "500.00 por servicio |  |"}
        )
        valid_raw = json.dumps(
            {"service_cost": {"raw_value": "500.00 por servicio"}, "notes": None}
        )
        responses = [
            SimpleNamespace(message=SimpleNamespace(content=invalid_raw)),
            SimpleNamespace(message=SimpleNamespace(content=valid_raw)),
        ]
        config = PipelineConfig(ollama_retries=1)

        with tempfile.TemporaryDirectory() as tmpdir, patch("pipeline.extract.chat", side_effect=responses) as mocked:
            result = call_structured("model", self.SCHEMA, "one insured", config, Path(tmpdir), tag="insured_7")
            debug_path = Path(tmpdir) / "debug_insured_7_attempt_1.txt"
            self.assertEqual(debug_path.read_text(encoding="utf-8"), invalid_raw)

        self.assertEqual(mocked.call_count, 2)
        self.assertIsNone(result["notes"])


if __name__ == "__main__":
    unittest.main()
