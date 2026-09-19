from __future__ import annotations

import unittest

from pathlib import Path
import tempfile

from pipeline.router import build_router_result, route_document, run_router_for_artifacts, score_document_ramo


class DocumentRouterTests(unittest.TestCase):
    def test_scores_every_ramo_and_preserves_matching_evidence(self):
        extracted = {
            "policy": {
                "insurer": "GNP",
                "product_line": "Gastos Médicos Mayores",
                "plan_name": "Línea Azul Premier",
            }
        }
        docling = {
            "texts": [
                {
                    "text": "CERTIFICADO DE COBERTURA POR ASEGURADO con deducible y coaseguro",
                    "prov": [{"page_no": 2}],
                }
            ],
            "tables": [],
        }

        scores = score_document_ramo(extracted, docling)

        self.assertEqual(set(scores["scores"]), {"GMM", "VIDA", "AUTOS", "DAÑOS"})
        self.assertEqual(scores["scores"]["GMM"]["score"], 100.0)
        self.assertTrue(scores["scores"]["GMM"]["evidence"])
        self.assertEqual(scores["scores"]["GMM"]["evidence"][0]["signal_id"], "gmm_explicit_product")

    def test_confident_gmm_routes_to_gmm_branch(self):
        extracted = {"policy": {"product_line": "GMM", "plan_name": "Premier 300 Omnia"}}
        docling = {"texts": [{"text": "Coberturas y Servicios deducible coaseguro", "prov": [{"page_no": 1}]}]}

        _scores, result = route_document(extracted, docling)

        self.assertEqual(result["classified_ramo"], "GMM")
        self.assertEqual(result["route_to"], "GMM")
        self.assertFalse(result["manual_review_required"])

    def test_low_confidence_routes_unknown_for_manual_review(self):
        scores = {
            "minimum_confidence": 90.0,
            "scores": {
                "GMM": {"score": 40.0, "evidence": []},
                "VIDA": {"score": 45.0, "evidence": []},
                "AUTOS": {"score": 0.0, "evidence": []},
                "DAÑOS": {"score": 0.0, "evidence": []},
            },
        }

        result = build_router_result(scores)

        self.assertEqual(result["classified_ramo"], "UNKNOWN")
        self.assertEqual(result["route_to"], "MANUAL_REVIEW")
        self.assertTrue(result["manual_review_required"])

    def test_run_router_for_artifacts_writes_scores_and_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            (run_dir / "01_docling.json").write_text(
                '{"texts":[{"text":"CERTIFICADO DE COBERTURA POR ASEGURADO deducible coaseguro","prov":[{"page_no":1}]}],"tables":[]}',
                encoding="utf-8",
            )
            (run_dir / "02_extracted.json").write_text(
                '{"policy":{"product_line":"Gastos Médicos Mayores","plan_name":"Premier 300"}}',
                encoding="utf-8",
            )

            _scores, result = run_router_for_artifacts(run_dir)

            self.assertEqual(result["classified_ramo"], "GMM")
            self.assertTrue((run_dir / "router_scores.json").exists())
            self.assertTrue((run_dir / "router_result.json").exists())


if __name__ == "__main__":
    unittest.main()
