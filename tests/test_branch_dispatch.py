from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from process_policy import PipelineFailure, dispatch_ramo_branch
from pipeline.config import PipelineConfig


class BranchDispatchTests(unittest.TestCase):
    def test_gmm_dispatches_to_current_gnp_branch(self):
        parsed = {"policy": {"product_line": "GMM"}}
        branch_report = {"layout": "gnp_premier"}
        extracted = {"policy": {"product_line": "GMM"}}
        extraction_report = {"insured_count": 1}

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "process_policy.extract_policy_from_docling",
            return_value=(extracted, extraction_report),
        ) as mocked_extract, patch(
            "process_policy.process_gnp_policy",
            return_value=(parsed, branch_report),
        ) as mocked_parse:
            result, report, actual_extraction_report, extracted_path = dispatch_ramo_branch(
                "GMM",
                {"texts": [], "tables": []},
                PipelineConfig(),
                Path("policy.pdf"),
                Path(tmpdir),
            )
            self.assertEqual(extracted_path, Path(tmpdir) / "02_extracted.json")
            self.assertTrue(extracted_path.exists())

        mocked_extract.assert_called_once()
        mocked_parse.assert_called_once()
        self.assertEqual(result, parsed)
        self.assertEqual(report["ramo"], "GMM")
        self.assertEqual(report["branch"], "gnp_gmm")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(actual_extraction_report, extraction_report)

    def test_planned_branch_dispatch_reports_not_implemented(self):
        for ramo in ("VIDA", "AUTOS", "DAÑOS"):
            with self.subTest(ramo=ramo), self.assertRaisesRegex(PipelineFailure, f"routed to {ramo}"):
                dispatch_ramo_branch(ramo, {}, PipelineConfig(), Path("policy.pdf"), Path("."))


if __name__ == "__main__":
    unittest.main()
