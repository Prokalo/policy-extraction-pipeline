from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class PipelineConfig:
    model_name: str = "qwen3:8b"
    schema_path: Path = Path("schemas/gmm/policy/v3.json")
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

    def final_output_path(self, pdf_path: Path) -> Path:
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        return self.outputs_dir / f"{pdf_path.stem}_validated.json"


DEFAULT_CONFIG = PipelineConfig()
