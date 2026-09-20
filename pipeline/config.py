from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Mapping


DEFAULT_SCHEMA_PATHS = {
    "GMM": Path("schemas/gmm/policy/v3.json"),
    "VIDA": Path("schemas/vida/policy/v1.json"),
    "AUTOS": Path("schemas/autos/policy/v1.json"),
    "DAÑOS": Path("schemas/daños/policy/v1.json"),
}


@dataclass(frozen=True)
class PipelineConfig:
    model_name: str = "qwen3:8b"
    schema_path: Path = DEFAULT_SCHEMA_PATHS["GMM"]
    schema_paths: Mapping[str, Path] = field(default_factory=lambda: DEFAULT_SCHEMA_PATHS.copy())
    schema_path_overrides_all_branches: bool = False
    ramo_signals_path: Path = Path("config/router/ramo_signals.json")
    outputs_dir: Path = Path("outputs")
    runs_dir: Path = Path("audit_logs/runs")
    ollama_temperature: int = 0
    ollama_num_predict: int = 4096
    ollama_num_ctx: int = 32768
    ollama_retries: int = 2
    validation_failure_severity: str = "high"

    def ensure_dirs(self) -> None:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def make_run_dir(self, pdf_path: Path) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.runs_dir / f"{pdf_path.stem}_{timestamp}"
        run_dir.mkdir(parents=True, exist_ok=False)
        return run_dir

    def schema_path_for(self, ramo: str) -> Path:
        if self.schema_path_overrides_all_branches:
            return self.schema_path
        return self.schema_paths.get(ramo.upper(), self.schema_path)

    def final_output_path(self, pdf_path: Path, ramo: str | None = None) -> Path:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        return self.outputs_dir / f"{pdf_path.stem}_validated.json"


DEFAULT_CONFIG = PipelineConfig()
