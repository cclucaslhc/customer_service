from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


PARSER_VERSION = "2.1"
CHUNKER_VERSION = "2.0"


class DocumentParseError(ValueError):
    """A user-facing document parsing or conversion failure."""


@dataclass(slots=True)
class ContentBlock:
    block_type: str
    text: str
    title: str | None = None
    location: str = "正文"
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_text(self) -> str:
        return "\n".join(line.rstrip() for line in self.text.splitlines()).strip()


@dataclass(slots=True)
class ParsedDocument:
    filename: str
    document_type: str
    blocks: list[ContentBlock]
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_text(self) -> bool:
        return any(block.normalized_text() for block in self.blocks)


@dataclass(slots=True)
class KnowledgeChunk:
    section: str
    location: str
    content: str
    block_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
