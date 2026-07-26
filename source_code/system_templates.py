from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEMPLATE_DIR = Path("templates") / "system-docs"
MANAGED_TEMPLATE_KEYS = ("mcp_usage_guide", "workspace_index_guide")


@dataclass(frozen=True)
class SystemTemplate:
    key: str
    language: str
    version: int
    markdown: str
    source_sha256: str
    historical_normalized_sha256: tuple[str, ...]


def normalize_template_markdown(markdown: str) -> str:
    text = str(markdown or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def markdown_sha256(markdown: str) -> str:
    normalized = normalize_template_markdown(markdown)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def source_sha256(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def load_system_template(root: Path, key: str, language: str) -> SystemTemplate:
    template_root = root / TEMPLATE_DIR
    manifest_path = template_root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取系统文档模板清单：{manifest_path}") from exc

    templates = manifest.get("templates", {})
    entry = templates.get(key)
    if not isinstance(entry, dict):
        raise RuntimeError(f"系统文档模板不存在：{key}")

    files = entry.get("files", {})
    language_key = language if language in files else "zh-CN"
    filename = str(files.get(language_key) or "").strip()
    if not filename:
        raise RuntimeError(f"系统文档模板缺少语言文件：{key}/{language}")

    template_path = template_root / filename
    try:
        markdown = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取系统文档模板：{template_path}") from exc

    expected_hashes = entry.get("source_sha256", {})
    expected_hash = str(expected_hashes.get(language_key) or "").strip().casefold()
    actual_hash = source_sha256(markdown)
    if expected_hash and expected_hash != actual_hash:
        raise RuntimeError(f"系统文档模板哈希不匹配：{template_path}")

    history = entry.get("historical_normalized_sha256", {})
    historical = history.get(language_key, [])
    if not isinstance(historical, list):
        historical = []

    return SystemTemplate(
        key=key,
        language=language_key,
        version=int(entry.get("version") or 1),
        markdown=markdown,
        source_sha256=actual_hash,
        historical_normalized_sha256=tuple(str(item) for item in historical if item),
    )


def template_manifest(root: Path) -> dict[str, Any]:
    path = root / TEMPLATE_DIR / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))
