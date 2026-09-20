from __future__ import annotations

import argparse
import unittest
from pathlib import Path

from pipeline.config import PipelineConfig
from process_policy import build_config


class PipelineConfigTests(unittest.TestCase):
    def test_schema_path_for_uses_branch_specific_schema(self):
        config = PipelineConfig()

        self.assertEqual(config.schema_path_for("GMM"), Path("schemas/gmm/policy/v3.json"))
        self.assertEqual(config.schema_path_for("VIDA"), Path("schemas/vida/policy/v1.json"))
        self.assertEqual(config.schema_path_for("AUTOS"), Path("schemas/autos/policy/v1.json"))
        self.assertEqual(config.schema_path_for("DAÑOS"), Path("schemas/daños/policy/v1.json"))

    def test_schema_cli_override_applies_to_all_branches(self):
        args = argparse.Namespace(
            model="qwen3:8b",
            schema="schemas/custom/policy.json",
            ramo_signals="config/router/ramo_signals.json",
        )

        config = build_config(args)

        self.assertEqual(config.schema_path_for("GMM"), Path("schemas/custom/policy.json"))
        self.assertEqual(config.schema_path_for("VIDA"), Path("schemas/custom/policy.json"))


if __name__ == "__main__":
    unittest.main()
