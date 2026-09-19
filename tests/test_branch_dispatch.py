from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from process_policy import PipelineFailure, dispatch_ramo_branch


class BranchDispatchTests(unittest.TestCase):
    def test_gmm_dispatches_to_current_gnp_branch(self):
        parsed = {"policy": {"product_line": "GMM"}}
        branch_report = {"layout": "gnp_premier"}

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "process_policy.process_gnp_policy",
            return_value=(parsed, branch_report),
        ) as mocked:
            result, report = dispatch_ramo_branch("GMM", {}, Path("policy.pdf"), Path(tmpdir))

        mocked.assert_called_once()
        self.assertEqual(result, parsed)
        self.assertEqual(report["ramo"], "GMM")
        self.assertEqual(report["branch"], "gnp_gmm")
        self.assertEqual(report["status"], "completed")

    def test_planned_branch_dispatch_reports_not_implemented(self):
        for ramo in ("VIDA", "AUTOS", "DAÑOS"):
            with self.subTest(ramo=ramo), self.assertRaisesRegex(PipelineFailure, f"routed to {ramo}"):
                dispatch_ramo_branch(ramo, {}, Path("policy.pdf"), Path("."))


if __name__ == "__main__":
    unittest.main()
