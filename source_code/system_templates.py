from __future__ import annotations

import hashlib
import re


def normalize_template_markdown(markdown: str) -> str:
    text = str(markdown or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def markdown_sha256(markdown: str) -> str:
    normalized = normalize_template_markdown(markdown)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
