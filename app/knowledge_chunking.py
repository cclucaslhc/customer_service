from __future__ import annotations

import re
from dataclasses import asdict

from app.knowledge_models import ContentBlock, KnowledgeChunk, ParsedDocument


SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?；;])|\n+")


def _hard_split(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= target_chars:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + target_chars)
        pieces.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return [piece for piece in pieces if piece]


def _sentence_units(text: str, target_chars: int, overlap_chars: int) -> list[str]:
    units: list[str] = []
    for sentence in SENTENCE_BOUNDARY.split(text):
        value = sentence.strip()
        if not value:
            continue
        units.extend(_hard_split(value, target_chars, overlap_chars))
    return units


def _table_chunks(block: ContentBlock, target_chars: int) -> list[KnowledgeChunk]:
    lines = [line for line in block.normalized_text().splitlines() if line.strip()]
    if not lines:
        return []
    is_markdown_separator = bool(
        len(lines) >= 2
        and re.fullmatch(r"\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*", lines[1])
    )
    header_lines = lines[:2] if is_markdown_separator else lines[:1]
    data_lines = lines[len(header_lines) :]
    chunks: list[KnowledgeChunk] = []
    current = header_lines.copy()
    for line in data_lines:
        candidate = "\n".join([*current, line])
        if len(candidate) > target_chars and len(current) > len(header_lines):
            chunks.append(KnowledgeChunk(block.title or "表格", block.location, "\n".join(current), "table", block.metadata.copy()))
            current = [*header_lines, line]
        else:
            current.append(line)
    if current:
        chunks.append(KnowledgeChunk(block.title or "表格", block.location, "\n".join(current), "table", block.metadata.copy()))
    return chunks


def chunk_document(
    document: ParsedDocument,
    target_chars: int = 900,
    overlap_chars: int = 120,
    min_chars: int = 80,
) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    current_units: list[str] = []
    current_section = "正文"
    current_location = "正文"
    current_types: list[str] = []
    current_metadata: list[dict] = []

    def flush() -> None:
        nonlocal current_units, current_types, current_metadata
        content = "\n".join(current_units).strip()
        if content:
            chunks.append(
                KnowledgeChunk(
                    current_section,
                    current_location,
                    content,
                    current_types[0] if len(set(current_types)) == 1 else "mixed",
                    {"source_blocks": current_metadata},
                )
            )
        current_units = []
        current_types = []
        current_metadata = []

    for block_index, block in enumerate(document.blocks):
        text = block.normalized_text()
        if not text:
            continue
        section = block.title or block.location or "正文"
        if block.block_type == "heading":
            flush()
            current_section = section
            current_location = block.location
            current_units = [text]
            current_types = ["heading"]
            current_metadata = [{"block_index": block_index, **block.metadata}]
            continue
        if block.block_type == "table":
            flush()
            chunks.extend(_table_chunks(block, target_chars))
            current_section = section
            current_location = block.location
            continue
        if current_units and section != current_section:
            flush()
        current_section = section
        current_location = block.location
        for unit in _sentence_units(text, target_chars, overlap_chars):
            candidate = "\n".join([*current_units, unit]).strip()
            if current_units and len(candidate) > target_chars:
                previous_tail = current_units[-1]
                flush()
                if overlap_chars and len(previous_tail) <= overlap_chars:
                    current_units.append(previous_tail)
                    current_types.append(block.block_type)
                    current_metadata.append({"overlap": True, "block_index": block_index})
            current_units.append(unit)
            current_types.append(block.block_type)
            current_metadata.append({"block_index": block_index, **block.metadata})
    flush()

    if len(chunks) > 1 and len(chunks[-1].content) < min_chars:
        tail = chunks.pop()
        previous = chunks[-1]
        if previous.section == tail.section and len(previous.content) + len(tail.content) + 1 <= target_chars + min_chars:
            previous.content = f"{previous.content}\n{tail.content}"
            previous.metadata.setdefault("merged_tail", True)
        else:
            chunks.append(tail)
    return chunks


def chunks_as_dicts(chunks: list[KnowledgeChunk]) -> list[dict]:
    return [asdict(chunk) for chunk in chunks]
