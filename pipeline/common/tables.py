from __future__ import annotations

import re
from collections import defaultdict

from pipeline.common.text import norm


COMMON = "COMMON"


def page_sort_y(prov: dict) -> float:
    bbox = prov.get("bbox") or {}
    return float(max(bbox.get("t", 0), bbox.get("b", 0)))


def clean_evidence_text(text: str | None) -> str:
    """Remove Docling blank/table residue without discarding meaningful text."""
    cleaned_lines = []
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\|\s*)+", line):
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?", line):
            continue
        if "|" in line:
            cells = [norm(cell) for cell in line.split("|") if norm(cell)]
            if not cells:
                continue
            line = " ; ".join(cells)
        if not cleaned_lines or line != cleaned_lines[-1]:
            cleaned_lines.append(line)
    return " ".join(cleaned_lines)


def build_page_items(doc: dict) -> dict[int, list[dict]]:
    pages: dict[int, list[dict]] = defaultdict(list)
    for item in doc.get("texts", []):
        text = clean_evidence_text(item.get("text") or item.get("orig") or "")
        prov = item.get("prov") or []
        page_no = prov[0].get("page_no") if prov else None
        if text and page_no:
            pages[int(page_no)].append({
                "kind": "text",
                "label": item.get("label") or "text",
                "text": text,
                "sort_y": page_sort_y(prov[0]),
                "sort_idx": len(pages[int(page_no)]),
            })
    for idx, table in enumerate(doc.get("tables", [])):
        prov = table.get("prov") or []
        page_no = prov[0].get("page_no") if prov else None
        if not page_no:
            continue
        grid = (table.get("data") or {}).get("grid") or []
        rows = []
        for row in grid:
            vals = []
            for column, cell in enumerate(row, start=1):
                raw = (cell.get("text") or "") if isinstance(cell, dict) else str(cell)
                cleaned = clean_evidence_text(raw)
                if cleaned:
                    vals.append(f"C{column}: {cleaned}")
            if vals:
                rows.append(" ; ".join(vals))
        if rows:
            pages[int(page_no)].append({
                "kind": "table",
                "label": f"TABLE {idx}",
                "text": "\n".join(rows),
                "sort_y": page_sort_y(prov[0]),
                "sort_idx": len(pages[int(page_no)]),
            })
    ordered = {}
    for page_no, items in pages.items():
        ordered[page_no] = sorted(items, key=lambda item: (-item["sort_y"], item["sort_idx"]))
    return dict(sorted(ordered.items()))


def format_page_item(item: dict) -> str:
    return f"[{item['label']}] {item['text']}"


def build_page_evidence(doc: dict) -> dict[int, list[str]]:
    pages: dict[int, list[str]] = defaultdict(list)
    for page_no, items in build_page_items(doc).items():
        pages[page_no] = [format_page_item(item) for item in items]
    return dict(sorted(pages.items()))


def render_pages(pages: dict[int, list[str]], nums: list[int]) -> str:
    out = []
    for page_no in nums:
        if page_no in pages:
            out.append(f"\n===== PAGE {page_no} =====")
            out.extend(pages[page_no])
    return "\n".join(out)


def flat_page_text(pages: dict[int, list[str]], page_no: int) -> str:
    return "\n".join(pages.get(page_no, []))


def page_text_from_items(items: list[dict]) -> str:
    return "\n".join(item.get("text", "") for item in items)


def render_page_items(items: list[dict]) -> str:
    return "\n".join(format_page_item(item) for item in items)


def combine_span_cells(lines, span):
    chunk = lines[span["start"]: span["end"] + 1]
    return {
        "sum": norm(" ".join(item["sum"] for item in chunk if item["sum"])),
        "ded": norm(" ".join(item["ded"] for item in chunk if item["ded"])),
        "coins": norm(" ".join(item["coins"] for item in chunk if item["coins"])),
    }


def group_visual_lines(page, tol=0.8):
    words = sorted(page.get_text("words"), key=lambda item: (item[1], item[0]))
    lines = []
    for word in words:
        y = word[1]
        target = None
        for line in reversed(lines[-4:]):
            if abs(line["y"] - y) <= tol:
                target = line
                break
        if target is None:
            target = {"y": y, "words": []}
            lines.append(target)
        target["words"].append(word)
        target["y"] = sum(item[1] for item in target["words"]) / len(target["words"])
    for line in lines:
        line["words"].sort(key=lambda item: item[0])
        line["text"] = norm(" ".join(item[4] for item in line["words"]))
    lines.sort(key=lambda item: item["y"])
    return lines


def cell_text(cell):
    if isinstance(cell, dict):
        return norm(cell.get("text"))
    return norm(str(cell))


def find_doc_text(doc, cref):
    for item in doc.get("texts", []):
        if item.get("self_ref") == cref:
            return norm(item.get("text") or item.get("orig"))
    return None
