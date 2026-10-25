"""Strip HTML boilerplate and extract clean text."""
from __future__ import annotations

import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)


def strip_html(html: str) -> str:
    """Return plain text extracted from an HTML string."""
    extractor = _TextExtractor()
    extractor.feed(html)
    text = " ".join(extractor.parts)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
