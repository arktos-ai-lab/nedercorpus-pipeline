"""Core document model: text + metadata, JSONL serialization."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from pipeline.schema import DocumentMeta


@dataclass
class Document:
    text: str
    meta: DocumentMeta
    # Optionally preserve the original when a normalized version is stored
    original_text: str | None = None

    @property
    def id(self) -> str:
        return self.meta.id

    def to_dict(self) -> dict:
        d = {"text": self.text}
        if self.original_text is not None:
            d["original_text"] = self.original_text
        d["meta"] = self.meta.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        meta = DocumentMeta.from_dict(d["meta"])
        return cls(
            text=d["text"],
            meta=meta,
            original_text=d.get("original_text"),
        )

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> "Document":
        return cls.from_dict(json.loads(line))


# ---------------------------------------------------------------------------
# Convenience I/O helpers
# ---------------------------------------------------------------------------

def write_jsonl(documents: Iterator[Document], path: Path) -> int:
    """Write documents to a .jsonl file. Returns number of documents written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for doc in documents:
            f.write(doc.to_jsonl() + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[Document]:
    """Yield Document objects from a .jsonl file."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Document.from_jsonl(line)
            except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                import logging as _logging
                _logging.getLogger(__name__).debug(
                    "Skipping bad line %d in %s", lineno, path.name
                )


def make_id(source: str, raw_id: str | None = None) -> str:
    if raw_id:
        return f"{source}:{raw_id}"
    return f"{source}:{uuid.uuid4().hex}"
