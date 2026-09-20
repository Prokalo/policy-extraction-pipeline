from __future__ import annotations

import json
from pathlib import Path

from docling.document_converter import DocumentConverter


COMMON = "COMMON"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def convert_pdf_to_docling_dict(pdf_path: Path) -> dict:
    converter = DocumentConverter()
    result = converter.convert(pdf_path)
    return json.loads(result.document.model_dump_json())


def save_json(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
