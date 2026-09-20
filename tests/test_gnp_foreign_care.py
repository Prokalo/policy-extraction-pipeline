from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipeline.branches.gmm.parser import extract_foreign_care_matrix_from_doc, rebuild_foreign_care_condition
from pipeline.branches.gmm.validate import reconcile_condition_warnings
from pipeline.common.validation import sanitize_strings_recursive


def foreign_care_doc(page_no: int = 4) -> dict:
    grid = [
        [
            {"text": "Premier 100"},
            {"text": "Premier 100"},
            {"text": "Premier 100"},
            {"text": "Premier 200"},
            {"text": "Premier 200"},
            {"text": "Premier 300"},
            {"text": "Premier 300"},
        ],
        [
            {"text": "Región"},
            {"text": "Primeros $100,000 pesos"},
            {"text": "Resto del gasto"},
            {"text": "Primeros $100,000 pesos"},
            {"text": "Resto del gasto"},
            {"text": "Primeros $100,000 pesos"},
            {"text": "Resto del gasto"},
        ],
        [{"text": "Metropolitano (1)"}, {"text": "50%"}, {"text": "25%"}, {"text": "40%"}, {"text": "20%"}, {"text": "35%"}, {"text": "17.5%"}],
        [{"text": "Noreste (2)"}, {"text": "40%"}, {"text": "20%"}, {"text": "35%"}, {"text": "17.5%"}, {"text": "30%"}, {"text": "15%"}],
        [{"text": "Noroeste (3)"}, {"text": "30%"}, {"text": "15%"}, {"text": "25%"}, {"text": "12.5%"}, {"text": "20%"}, {"text": "10%"}],
        [{"text": "Occidente (4)"}, {"text": "30%"}, {"text": "15%"}, {"text": "25%"}, {"text": "12.5%"}, {"text": "20%"}, {"text": "10%"}],
        [{"text": "Sureste (5)"}, {"text": "30%"}, {"text": "15%"}, {"text": "25%"}, {"text": "12.5%"}, {"text": "20%"}, {"text": "10%"}],
    ]
    return {
        "texts": [
            {
                "self_ref": "#/texts/foreign_note",
                "text": "* Esta cobertura no aplica para Premier 400.",
            }
        ],
        "tables": [
            {
                "prov": [{"page_no": page_no}],
                "data": {"grid": grid},
                "footnotes": [{"cref": "#/texts/foreign_note"}],
            }
        ],
    }


def foreign_care_condition() -> dict:
    return {
        "condition_type": "Cobertura de atención en el extranjero",
        "scope": "Ambigua",
        "description": "Tabla ambigua",
        "rules": [
            {
                "criteria": "Región: Metropolitano (1)",
                "raw_value": "Premier 100 | Premier 100 | Premier 100 | Premier 200 | Premier 200 | Premier 300 | Premier 300",
                "amount": 50,
                "secondary_amount": 25,
                "currency": "MXN",
                "percentage": 50,
                "secondary_percentage": 25,
                "unit": "%",
                "effective_start_date": None,
                "effective_end_date": None,
                "notes": "Premier 100: 50% | 25%",
            }
        ],
        "source_page": 4,
    }


class GnpForeignCareTests(unittest.TestCase):
    def test_extract_foreign_care_matrix_preserves_plan_relationships_and_note(self):
        parsed = extract_foreign_care_matrix_from_doc(foreign_care_doc())

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["source_page"], 4)
        self.assertEqual(parsed["note"], "Esta cobertura no aplica para Premier 400.")
        self.assertEqual(parsed["matrix"][0]["region"], "Metropolitano")
        self.assertEqual(
            parsed["matrix"][0]["plans"]["Premier 300"],
            {"first_100k_percentage": 35.0, "remaining_expense_percentage": 17.5},
        )
        self.assertEqual(
            parsed["matrix"][2]["plans"]["Premier 200"],
            {"first_100k_percentage": 25.0, "remaining_expense_percentage": 12.5},
        )

    def test_rebuild_foreign_care_condition_selects_policy_plan(self):
        expectations = {
            "PREMIER 100 OMNIA": [("Metropolitano", "50% / 25%"), ("Noreste", "40% / 20%"), ("Noroeste", "30% / 15%")],
            "PREMIER 200 OMNIA": [("Metropolitano", "40% / 20%"), ("Noreste", "35% / 17.5%"), ("Noroeste", "25% / 12.5%")],
            "PREMIER 300 OMNIA": [
                ("Metropolitano", "35% / 17.5%"),
                ("Noreste", "30% / 15%"),
                ("Noroeste", "20% / 10%"),
                ("Occidente", "20% / 10%"),
                ("Sureste", "20% / 10%"),
            ],
        }

        for plan_name, expected_rows in expectations.items():
            with self.subTest(plan_name=plan_name):
                data = {
                    "policy": {"plan_name": plan_name, "plan_raw_text": f"Plan {plan_name}"},
                    "policy_conditions": [foreign_care_condition()],
                }
                with tempfile.TemporaryDirectory() as tmpdir:
                    run_dir = Path(tmpdir)
                    (run_dir / "01_docling.json").write_text(json.dumps(foreign_care_doc()), encoding="utf-8")
                    changes = rebuild_foreign_care_condition(data, run_dir)

                self.assertEqual(changes, 1)
                condition = data["policy_conditions"][0]
                self.assertEqual(condition["scope"], "Aplica a Premier 100, Premier 200 y Premier 300; no aplica a Premier 400")
                region_rules = [rule for rule in condition["rules"] if rule["criteria"].startswith("Región: ")]
                self.assertEqual(len(region_rules), 5)
                pairs = [(rule["criteria"].replace("Región: ", ""), rule["raw_value"]) for rule in region_rules]
                if "300" in plan_name:
                    self.assertEqual(pairs, expected_rows)
                else:
                    self.assertEqual(pairs[:3], expected_rows)
                self.assertEqual(condition["rules"][-1]["criteria"], "Aplicabilidad de plan")
                self.assertEqual(condition["rules"][-1]["raw_value"], "Esta cobertura no aplica para Premier 400.")
                self.assertNotIn("Premier 400", " ".join(rule["criteria"] for rule in region_rules))

    def test_sanitize_strings_recursive_removes_model_control_artifacts(self):
        data = {
            "policy_conditions": [
                {
                    "condition_type": "Tope de coaseguro",
                    "scope": "Aplica",
                    "description": "<think>texto</think>",
                    "rules": [
                        {"criteria": "Tipo", "raw_value": "Único /no_think", "notes": "Dato </think> final"},
                    ],
                    "source_page": 4,
                }
            ]
        }

        changes = sanitize_strings_recursive(data)

        self.assertEqual(len(changes), 3)
        rule = data["policy_conditions"][0]["rules"][0]
        self.assertEqual(data["policy_conditions"][0]["description"], "texto")
        self.assertEqual(rule["raw_value"], "Único")
        self.assertEqual(rule["notes"], "Dato final")

    def test_reconcile_condition_warnings_removes_only_stale_premier_400_warning(self):
        data = {
            "policy_conditions": [
                {
                    "condition_type": "Cobertura de atención en el extranjero",
                    "scope": "Aplica a Premier 100, Premier 200 y Premier 300; no aplica a Premier 400",
                    "description": "Cobertura por región",
                    "rules": [
                        {
                            "criteria": "Aplicabilidad de plan",
                            "raw_value": "Esta cobertura no aplica para Premier 400.",
                            "amount": None,
                            "secondary_amount": None,
                            "currency": None,
                            "percentage": None,
                            "secondary_percentage": None,
                            "unit": None,
                            "effective_start_date": None,
                            "effective_end_date": None,
                            "notes": "Esta cobertura no aplica para Premier 400.",
                        }
                    ],
                    "source_page": 4,
                }
            ],
            "validation": {
                "warnings": [
                    {
                        "field": "condition",
                        "issue": "The section contains a footnote indicating that this coverage does not apply to Premier 400. This information is not included in the extracted condition object as it is not part of the rules or description for the coverage itself.",
                        "severity": "low",
                        "source_page": 7,
                    },
                    {
                        "field": "policy_conditions",
                        "issue": "Another legitimate condition warning.",
                        "severity": "medium",
                        "source_page": 4,
                    },
                ]
            },
        }

        removed = reconcile_condition_warnings(data)

        self.assertEqual(removed, 1)
        self.assertEqual(len(data["validation"]["warnings"]), 1)
        self.assertEqual(data["validation"]["warnings"][0]["issue"], "Another legitimate condition warning.")


if __name__ == "__main__":
    unittest.main()
