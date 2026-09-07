from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.config import PipelineConfig
from pipeline.extract import extract_policy_from_docling
from pipeline.parsers.gnp import clean_policy_conditions


def text_item(page_no: int, text: str, y: float, label: str = "text") -> dict:
    return {
        "text": text,
        "label": label,
        "prov": [{"page_no": page_no, "bbox": {"t": y, "b": y - 10}}],
    }


def build_doc() -> dict:
    texts = [
        text_item(1, "Póliza GNP", 700, "section_header"),
        text_item(3, "CERTIFICADO DE COBERTURA POR ASEGURADO", 720, "section_header"),
        text_item(3, "Asegurado 1", 700, "section_header"),
        text_item(4, "Condiciones Especiales", 710, "section_header"),
        text_item(4, "- Cobertura de preexistencia", 690, "section_header"),
        text_item(4, "No aplica enfermedades preexistentes.", 670),
        text_item(4, "- Cobertura de atención en el extranjero", 650, "section_header"),
        text_item(4, "Monto máximo en el extranjero.", 630),
        text_item(4, "- Tope de coaseguro", 610, "section_header"),
        text_item(4, "Tope de coaseguro de $120,000 pesos.", 590),
        text_item(4, "Eliminación o reducción de periodos de espera", 570, "section_header"),
        text_item(4, "Cobertura previa del 15/08/2010 al 15/08/2012.", 550),
        text_item(4, "- Monto para Productos de Terapia génica", 530, "section_header"),
        text_item(4, "Nacional $3,500,000 pesos.", 510),
        text_item(4, "- Auxiliares mecánicos electrónicos y/o computarizados", 490, "section_header"),
        text_item(4, "Nacional $222,000 pesos.", 470),
        text_item(6, "CERTIFICADO DE COBERTURA POR ASEGURADO", 720, "section_header"),
        text_item(6, "Asegurado 2", 700, "section_header"),
        text_item(7, "Condiciones Especiales", 710, "section_header"),
        text_item(7, "- Cobertura de preexistencia", 690, "section_header"),
        text_item(7, "No aplica enfermedades preexistentes.", 670),
        text_item(7, "- Cobertura de atención en el extranjero", 650, "section_header"),
        text_item(7, "Monto máximo en el extranjero.", 630),
        text_item(7, "- Tope de coaseguro", 610, "section_header"),
        text_item(7, "Tope de coaseguro de $120,000 pesos.", 590),
        text_item(7, "Eliminación o reducción de periodos de espera", 570, "section_header"),
        text_item(7, "Cobertura previa del 20/09/2011 al 20/09/2013.", 550),
        text_item(7, "- Monto para Productos de Terapia génica", 530, "section_header"),
        text_item(7, "Nacional $3,500,000 pesos.", 510),
        text_item(7, "- Auxiliares mecánicos electrónicos y/o computarizados", 490, "section_header"),
        text_item(7, "Nacional $222,000 pesos.", 470),
    ]
    return {"texts": texts, "tables": []}


def meta_payload() -> dict:
    return {
        "document": {"document_type": "policy", "language": "es", "page_count": 7},
        "policy": {
            "insurer": "GNP",
            "policy_number": "123",
            "version": None,
            "renewal_number": None,
            "product_line": None,
            "plan_raw_text": None,
            "plan_name": None,
            "hospital_level": None,
            "scheme_name": None,
            "issue_date": None,
            "coverage_start_date": None,
            "coverage_end_date": None,
            "term_days": None,
            "currency": None,
            "movement_type": None,
            "movement_description": None,
            "source_page": 1,
        },
        "policyholder": {
            "name": "ACME",
            "customer_code": "C1",
            "tax_id": None,
            "address": None,
            "email": None,
            "phone": None,
            "source_page": 1,
        },
        "premium_summary": {
            "net_premium": None,
            "installment_surcharge": None,
            "policy_fee": None,
            "tax_rate_percent": None,
            "tax_amount": None,
            "total_amount": None,
            "payment_method": None,
            "payment_channel": None,
            "source_page": 1,
        },
        "agent": {"name": None, "agent_code": None, "source_page": 1},
        "regulatory": {"registration_number": None, "registration_date": None, "source_page": None},
        "warnings": [],
    }


def insured_payload(number: int, page_no: int) -> dict:
    return {
        "insured_number": number,
        "role": "Titular" if number == 1 else "Dependiente",
        "name": f"Asegurado {number}",
        "customer_code": f"C{number}",
        "birth_date": None,
        "gender": None,
        "seniority_date": None,
        "coverage_scope": "Nacional",
        "premium": {
            "net_premium": None,
            "installment_surcharge": None,
            "policy_fee": None,
            "tax_amount": None,
            "total_amount": None,
        },
        "coverages": [],
        "conditions": [],
        "source_page": page_no,
        "warnings": [],
    }


def condition_payload(heading: str, page_no: int) -> dict:
    return {
        "condition": {
            "condition_type": heading,
            "scope": "Aplica",
            "description": f"{heading} page {page_no}",
            "rules": [],
            "source_page": page_no,
        },
        "warnings": [],
    }


class ConditionSectionTests(unittest.TestCase):
    def test_extract_policy_splits_repeated_condition_pages(self):
        doc = build_doc()
        seen_tags = []

        def fake_call(model, schema, prompt, config, debug_dir, tag):
            seen_tags.append(tag)
            if tag == "meta":
                return meta_payload()
            if tag == "insured_1":
                return insured_payload(1, 3)
            if tag == "insured_2":
                return insured_payload(2, 6)
            if tag.startswith("conditions_4_"):
                return condition_payload(prompt.split('"')[1], 4)
            if tag.startswith("conditions_7_"):
                return condition_payload(prompt.split('"')[1], 7)
            raise AssertionError(tag)

        with tempfile.TemporaryDirectory() as tmpdir, patch("pipeline.extract.call_structured", side_effect=fake_call):
            final, report = extract_policy_from_docling(doc, PipelineConfig(), Path(tmpdir))

        self.assertEqual(report["condition_page_count"], 2)
        self.assertEqual([page["page"] for page in report["condition_pages"]], [4, 7])
        expected = [
            "Cobertura de preexistencia",
            "Cobertura de atención en el extranjero",
            "Tope de coaseguro",
            "Eliminación o reducción de periodos de espera",
            "Monto para Productos de Terapia génica",
            "Auxiliares mecánicos electrónicos y/o computarizados",
        ]
        self.assertEqual(report["condition_pages"][0]["extracted_headings"], expected)
        self.assertEqual(report["condition_pages"][1]["extracted_headings"], expected)
        self.assertEqual(final["policy_conditions"][0]["source_page"], 4)
        self.assertEqual(final["policy_conditions"][-1]["source_page"], 7)
        self.assertEqual(len([tag for tag in seen_tags if tag.startswith("conditions_")]), 12)

    def test_clean_policy_conditions_routes_waiting_periods_and_dedupes(self):
        data = {
            "insureds": [
                {"insured_number": 1, "source_page": 3, "conditions": []},
                {"insured_number": 2, "source_page": 6, "conditions": []},
            ],
            "policy_conditions": [
                {"condition_type": "Cobertura de preexistencia", "scope": "Aplica", "description": "a", "rules": [], "source_page": 4},
                {"condition_type": "Cobertura de preexistencia", "scope": "Aplica", "description": "a", "rules": [], "source_page": 7},
                {"condition_type": "Cobertura de atención en el extranjero", "scope": "Aplica", "description": "b", "rules": [], "source_page": 4},
                {"condition_type": "Cobertura de atención en el extranjero", "scope": "Aplica", "description": "b", "rules": [], "source_page": 7},
                {"condition_type": "Tope de coaseguro", "scope": "Aplica", "description": "c", "rules": [], "source_page": 4},
                {"condition_type": "Tope de coaseguro", "scope": "Aplica", "description": "c", "rules": [], "source_page": 7},
                {
                    "condition_type": "Eliminación o reducción de periodos de espera",
                    "scope": "Aplica",
                    "description": "wait 1",
                    "rules": [],
                    "source_page": 4,
                },
                {
                    "condition_type": "Eliminación o reducción de periodos de espera",
                    "scope": "Aplica",
                    "description": "wait 2",
                    "rules": [],
                    "source_page": 7,
                },
                {"condition_type": "Monto para Productos de Terapia génica", "scope": "Aplica", "description": "d", "rules": [], "source_page": 4},
                {"condition_type": "Monto para Productos de Terapia génica", "scope": "Aplica", "description": "d", "rules": [], "source_page": 7},
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Aplica",
                    "description": "e",
                    "rules": [],
                    "source_page": 4,
                },
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Aplica",
                    "description": "e",
                    "rules": [],
                    "source_page": 7,
                },
            ],
        }

        raw_count, final_count, moved, normalized_amounts, normalized_waiting = clean_policy_conditions(data)

        self.assertEqual(raw_count, 12)
        self.assertEqual(final_count, 5)
        self.assertEqual(moved, 2)
        self.assertEqual(normalized_amounts, 0)
        self.assertEqual(normalized_waiting, 0)
        self.assertEqual(len(data["insureds"][0]["conditions"]), 1)
        self.assertEqual(len(data["insureds"][1]["conditions"]), 1)

    def test_extract_policy_warns_when_section_still_fails(self):
        doc = build_doc()

        def fake_call(model, schema, prompt, config, debug_dir, tag):
            if tag == "meta":
                return meta_payload()
            if tag == "insured_1":
                return insured_payload(1, 3)
            if tag == "insured_2":
                return insured_payload(2, 6)
            if tag == "conditions_4_tope_de_coaseguro":
                raise RuntimeError("conditions_4_tope_de_coaseguro failed after retries: Unterminated string")
            if tag.startswith("conditions_4_"):
                return condition_payload(prompt.split('"')[1], 4)
            if tag.startswith("conditions_7_"):
                return condition_payload(prompt.split('"')[1], 7)
            raise AssertionError(tag)

        with tempfile.TemporaryDirectory() as tmpdir, patch("pipeline.extract.call_structured", side_effect=fake_call):
            final, report = extract_policy_from_docling(doc, PipelineConfig(), Path(tmpdir))

        self.assertTrue(any(
            warning["severity"] == "high"
            and 'Condition section "Tope de coaseguro" on page 4 failed' in warning["issue"]
            for warning in final["validation"]["warnings"]
        ))

    def test_clean_policy_conditions_structures_auxiliares_amount_and_waiting_rules(self):
        data = {
            "insureds": [
                {"insured_number": 1, "source_page": 3, "conditions": []},
                {"insured_number": 2, "source_page": 6, "conditions": []},
            ],
            "policy_conditions": [
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Nacional",
                    "description": "Monto máximo a pagar: $ 222,000 MXN. El alcance general continúa.",
                    "rules": [],
                    "source_page": 4,
                },
                {
                    "condition_type": "Eliminación o reducción de periodos de espera",
                    "scope": "El asegurado cuenta con el beneficio de reducción de periodos de espera para tratamientos contratados, tomando en consideración que el asegurado contó con cobertura de gastos médicos mayores del: 15/08/2010 al: 15/08/2012.",
                    "description": "Quedan excluidas enfermedades y/o accidentes ocurridos antes de la fecha de antigüedad descrita en esta póliza.",
                    "rules": [
                        {
                            "criteria": "Para este beneficio se considera la fecha del 15/08/2010",
                            "raw_value": "15/08/2010",
                            "amount": None,
                            "secondary_amount": None,
                            "currency": None,
                            "percentage": None,
                            "secondary_percentage": None,
                            "unit": None,
                            "effective_start_date": "15/08/2010",
                            "effective_end_date": "15/08/2012",
                            "notes": None,
                        }
                    ],
                    "source_page": 4,
                },
            ],
        }

        raw_count, final_count, moved, normalized_amounts, normalized_waiting = clean_policy_conditions(data)

        self.assertEqual((raw_count, final_count, moved), (2, 1, 1))
        self.assertEqual(normalized_amounts, 1)
        self.assertEqual(normalized_waiting, 1)
        aux = data["policy_conditions"][0]
        self.assertEqual(aux["description"], "Monto máximo a pagar")
        self.assertEqual(aux["rules"][0]["amount"], 222000.0)
        self.assertEqual(aux["rules"][0]["currency"], "MXN")
        self.assertEqual(aux["rules"][0]["raw_value"], "$ 222,000 pesos")

        waiting = data["insureds"][0]["conditions"][0]
        self.assertIn("beneficio de reducción de periodos de espera", waiting["description"])
        self.assertIn("Quedan excluidas", waiting["description"])
        self.assertEqual(
            [rule["criteria"] for rule in waiting["rules"]],
            ["Cobertura previa de gastos médicos mayores", "Fecha considerada para el beneficio"],
        )
        self.assertEqual(waiting["rules"][0]["effective_start_date"], "15/08/2010")
        self.assertEqual(waiting["rules"][0]["effective_end_date"], "15/08/2012")
        self.assertEqual(waiting["rules"][1]["effective_start_date"], "15/08/2010")
        self.assertIsNone(waiting["rules"][1]["effective_end_date"])

    def test_clean_policy_conditions_preserves_provenance_for_identical_repeated_certificate_conditions(self):
        data = {
            "insureds": [
                {"insured_number": 1, "source_page": 3, "conditions": []},
                {"insured_number": 2, "source_page": 6, "conditions": []},
            ],
            "policy_conditions": [
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Nacional",
                    "description": "Monto máximo a pagar: $ 222,000 MXN.",
                    "rules": [],
                    "source_page": 4,
                },
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Nacional",
                    "description": "Monto máximo a pagar: $ 222,000 MXN.",
                    "rules": [],
                    "source_page": 7,
                },
            ],
        }

        _, final_count, _, normalized_amounts, _ = clean_policy_conditions(data)

        self.assertEqual(final_count, 1)
        self.assertEqual(normalized_amounts, 2)
        cond = data["policy_conditions"][0]
        self.assertEqual(cond["applicability_scope"], "Insured")
        self.assertEqual(cond["applies_to_insured_numbers"], [1, 2])
        self.assertEqual(cond["source_pages"], [4, 7])
        self.assertEqual(cond["source_page"], 4)

    def test_clean_policy_conditions_does_not_merge_future_different_per_insured_conditions(self):
        data = {
            "insureds": [
                {"insured_number": 1, "source_page": 3, "conditions": []},
                {"insured_number": 2, "source_page": 6, "conditions": []},
            ],
            "policy_conditions": [
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Nacional",
                    "description": "Monto máximo a pagar: $ 222,000 MXN.",
                    "rules": [],
                    "source_page": 4,
                },
                {
                    "condition_type": "Auxiliares mecánicos electrónicos y/o computarizados",
                    "scope": "Nacional",
                    "description": "Monto máximo a pagar: $ 111,000 MXN.",
                    "rules": [],
                    "source_page": 7,
                },
            ],
        }

        _, final_count, _, _, _ = clean_policy_conditions(data)

        self.assertEqual(final_count, 2)
        amounts = sorted(cond["rules"][0]["amount"] for cond in data["policy_conditions"])
        self.assertEqual(amounts, [111000.0, 222000.0])
        applies = sorted(tuple(cond.get("applies_to_insured_numbers", [])) for cond in data["policy_conditions"])
        self.assertEqual(applies, [(1,), (2,)])


if __name__ == "__main__":
    unittest.main()
