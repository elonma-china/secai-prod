#!/usr/bin/env python3
"""Extract Hướng dẫn sử dụng IAS docx into breadcrumb-tagged, size-capped chunks.

Two layers: (1) split at Heading 1/Heading 2 boundaries for chương/mục structure,
merging near-empty parent headings into their first child section; (2) size-cap
any section over CHUNK_MAX_CHARS into overlapping sub-chunks cut at paragraph
boundaries. See /home/ict/.claude/plans/swift-fluttering-knuth.md for rationale
(measured size distribution: median ~1.9k chars, max 48.4k chars across 66 sections).

Usage: python3 extract_docx.py <input.docx> <output.jsonl>
"""
from __future__ import annotations

import json
import sys

import docx

CHUNK_MAX_CHARS = 1200
CHUNK_TARGET_CHARS = 900
OVERLAP_CHARS = 150
PARENT_MERGE_THRESHOLD = 200


def load_sections(path: str) -> list[dict]:
    """Return [{chuong, tieu_de, paragraphs: [str, ...]}] in document order."""
    d = docx.Document(path)
    sections: list[dict] = []
    chuong_title = None
    cur = None
    for p in d.paragraphs:
        style = p.style.name
        text = p.text.strip()
        if style == "Heading 1":
            if not text:
                continue
            chuong_title = text
            cur = {"chuong": chuong_title, "tieu_de": None, "paragraphs": []}
            sections.append(cur)
        elif style == "Heading 2":
            if not text:
                continue
            cur = {"chuong": chuong_title, "tieu_de": text, "paragraphs": []}
            sections.append(cur)
        else:
            if text and cur is not None:
                cur["paragraphs"].append(text)
    return sections


def merge_empty_parents(sections: list[dict]) -> list[dict]:
    """A Heading-1-only section (no tieu_de) whose own body is short is almost always
    just the chapter banner — its real content lives in the first Heading-2 child.
    Prepend its paragraphs there instead of emitting a near-empty standalone chunk."""
    merged: list[dict] = []
    pending_parent_paras: list[str] = []
    for sec in sections:
        body_len = sum(len(t) for t in sec["paragraphs"])
        if sec["tieu_de"] is None:
            if body_len < PARENT_MERGE_THRESHOLD:
                pending_parent_paras = sec["paragraphs"]
                continue
            merged.append(sec)
            pending_parent_paras = []
            continue
        if pending_parent_paras:
            sec = {**sec, "paragraphs": pending_parent_paras + sec["paragraphs"]}
            pending_parent_paras = []
        merged.append(sec)
    return merged


def breadcrumb(sec: dict, part: int | None = None, total: int | None = None) -> str:
    label = sec["chuong"] if sec["tieu_de"] is None else f"{sec['chuong']} > {sec['tieu_de']}"
    if part is not None:
        label += f" — phần {part}/{total}"
    return label


def pack_paragraphs(paragraphs: list[str]) -> list[str]:
    """Greedy-pack paragraphs into ~CHUNK_TARGET_CHARS chunks, carrying the tail of
    each chunk into the next as overlap so a step-by-step procedure split across the
    boundary still reads coherently in either chunk."""
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for para in paragraphs:
        if cur and cur_len + len(para) > CHUNK_MAX_CHARS:
            chunks.append("\n".join(cur))
            overlap: list[str] = []
            overlap_len = 0
            for prev in reversed(cur):
                if overlap_len >= OVERLAP_CHARS:
                    break
                overlap.insert(0, prev)
                overlap_len += len(prev)
            cur = overlap
            cur_len = overlap_len
        cur.append(para)
        cur_len += len(para)
        if cur_len >= CHUNK_TARGET_CHARS:
            chunks.append("\n".join(cur))
            overlap = []
            overlap_len = 0
            for prev in reversed(cur):
                if overlap_len >= OVERLAP_CHARS:
                    break
                overlap.insert(0, prev)
                overlap_len += len(prev)
            cur = overlap
            cur_len = overlap_len
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def build_chunks(sections: list[dict]) -> list[dict]:
    out: list[dict] = []
    for sec in sections:
        body = "\n".join(sec["paragraphs"]).strip()
        if not body:
            continue
        if len(body) <= CHUNK_MAX_CHARS:
            out.append({
                "chuong": sec["chuong"], "tieu_de": sec["tieu_de"], "part": None,
                "text": f"{breadcrumb(sec)}\n\n{body}",
            })
            continue
        parts = pack_paragraphs(sec["paragraphs"])
        total = len(parts)
        for i, part_text in enumerate(parts, start=1):
            out.append({
                "chuong": sec["chuong"], "tieu_de": sec["tieu_de"], "part": i,
                "text": f"{breadcrumb(sec, i, total)}\n\n{part_text}",
            })
    for idx, rec in enumerate(out):
        rec["id"] = f"ias-howto-{idx:04d}"
    return out


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <input.docx> <output.jsonl>", file=sys.stderr)
        raise SystemExit(2)
    in_path, out_path = sys.argv[1], sys.argv[2]
    sections = merge_empty_parents(load_sections(in_path))
    chunks = build_chunks(sections)
    with open(out_path, "w", encoding="utf-8") as f:
        for rec in chunks:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    lens = [len(c["text"]) for c in chunks]
    print(f"sections={len(sections)} chunks={len(chunks)} "
          f"min={min(lens)} max={max(lens)} avg={sum(lens)//len(lens)}")


if __name__ == "__main__":
    main()
