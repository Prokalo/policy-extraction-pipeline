from __future__ import annotations

import re
import unicodedata


COMMON = "COMMON"


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def deaccent(text: str | None) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def keytext(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", deaccent(norm(text)).lower()).strip()


def keytext_loose(text: str | None) -> str:
    return re.sub(r"[^a-z0-9%$]+", " ", deaccent(norm(text)).lower()).strip()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", keytext(text)).strip("_") or "section"
