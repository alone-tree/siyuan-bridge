from __future__ import annotations

import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .cli import load_live_docs
from .client import SiYuanApiError, SiYuanClient, SiYuanConnectionError, SiYuanTimeoutError
from .config import Profile, detect_active_profile, load_config
from .ignore import (
    PrivacyRules,
    compile_rules,
    document_permission,
    filter_documents,
    load_privacy_rules,
    rule_matches_doc,
    write_privacy_rules_cache,
)
from .indexer import (
    KNOWLEDGE_BASE_DIR,
    build_notebook_overview,
    compute_word_count,
    ensure_notebooks_open,
    extract_snippet,
    format_date,
    load_docs,
    refresh_index,
    render_doc_tree,
    resolve_document,
    search_content,
)
from .agent_notebook import (
    AgentNotebookState,
    PrivacyRulesUnavailableError,
    is_privacy_rules_document,
    is_system_notebook_name,
    load_agent_notebook,
)
from .telemetry import (
    _resolve_proxy,
    _with_telemetry,
    ensure_session_id,
    get_effective_endpoint,
    load_anonymous_id,
    load_telemetry_config,
    set_siyuan_version,
    submit_feedback as _telemetry_submit_feedback,
)


from . import __version__

SERVER_NAME = "siyuan-bridge"
DEFAULT_SNIPPETS_PER_DOC = 5
MAX_REFERENCE_DETAILS_PER_BLOCK = 20
DEFAULT_REFERENCE_DOCUMENT_LIMIT = 10
MAX_REFERENCE_BLOCKS_PER_DOCUMENT = 3
MAX_REFERENCE_BLOCK_CHARACTERS = 2000
POST_WRITE_SYNC_TIMEOUT = 5.0
POST_WRITE_SYNC_INTERVAL = 0.25
ASSET_LARGE_FILE_THRESHOLD_BYTES = 20 * 1024 * 1024
SIYUAN_IMAGE_EXTENSIONS = frozenset({
    ".apng", ".ico", ".cur", ".jpg", ".jpe", ".jpeg", ".jfif", ".pjp",
    ".pjpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif", ".tiff", ".tif",
})


def workspace_index_age_days(updated: str, now: datetime | None = None) -> int | None:
    value = str(updated or "").strip()
    if len(value) < 8:
        return None
    fmt = "%Y%m%d%H%M%S" if len(value) >= 14 else "%Y%m%d"
    try:
        changed_at = datetime.strptime(value[:14] if len(value) >= 14 else value[:8], fmt)
    except ValueError:
        return None
    current = now or datetime.now()
    return max((current - changed_at).days, 0)


def format_siyuan_updated(updated: str) -> str:
    value = str(updated or "").strip()
    if len(value) >= 14:
        return (
            f"{value[:4]}-{value[4:6]}-{value[6:8]} "
            f"{value[8:10]}:{value[10:12]}"
        )
    if len(value) >= 8:
        return f"{value[:4]}-{value[4:6]}-{value[6:8]}"
    return "未知"

# ---------------------------------------------------------------------------
# Error codes for telemetry — category:detail two-level encoding
# category 用于聚合看板，detail 用于下钻诊断
# ---------------------------------------------------------------------------

# validation — AI 传参错误
_ERR_MISSING_PARAM    = "validation:missing_param"
_ERR_INVALID_ENUM     = "validation:invalid_enum"
_ERR_INVALID_TYPE     = "validation:invalid_type"
_ERR_OUT_OF_RANGE     = "validation:out_of_range"
_ERR_WRONG_SHAPE      = "validation:wrong_shape"
_ERR_OPERATION_ORDER  = "validation:operation_order"
_ERR_WRONG_TARGET     = "validation:wrong_target_type"
_ERR_INVALID_TABLE    = "validation:invalid_table"
_ERR_MISMATCH         = "validation:mismatch"
_ERR_MISSING_EDIT_RANGE = "validation:missing_edit_range"

# permission — 权限不足或未确认
_ERR_NOT_CONFIRMED    = "permission:not_confirmed"
_ERR_NOT_READ_WRITE   = "permission:not_read_write"
_ERR_PRIVACY_RULES    = "permission:privacy_rules"
_ERR_SQL_ADMIN        = "permission:sql_admin"
_ERR_SUBTREE_BLOCKED  = "permission:subtree_blocked"
_ERR_ANCESTOR_BLOCKED = "permission:ancestor_blocked"

# not_found — 目标不存在
_ERR_DOC_NOT_FOUND    = "not_found:document"
_ERR_NB_NOT_FOUND     = "not_found:notebook"
_ERR_PARENT_NOT_FOUND = "not_found:parent"
_ERR_BLOCK_NOT_FOUND  = "not_found:block_index"

# conflict — 状态不一致
_ERR_ALREADY_EXISTS      = "conflict:already_exists"
_ERR_AMBIGUOUS           = "conflict:ambiguous_path"
_ERR_STALE_BLOCK_ID      = "conflict:stale_block_id"
_ERR_STALE_DOCUMENT_PATH = "conflict:stale_document_path"
_ERR_STALE_CELL_VALUE    = "conflict:stale_cell_value"
_ERR_MULTI_DOC_OVERWRITE = "conflict:multi_doc_overwrite"
_ERR_REFERENCED_BLOCKS    = "conflict:referenced_blocks"
_ERR_DUPLICATE_ASSET_NAME = "conflict:duplicate_asset_name"

# api — 思源 API 层错误（从 SiYuanApiError 转换）
_ERR_SNAPSHOT_KEY   = "api:snapshot_key"
_ERR_SNAPSHOT_FAILED = "api:snapshot_failed"
_ERR_DUPLICATE_NO_ID = "api:duplicate_no_id"
_ERR_SYNC_TIMEOUT = "api:sync_timeout"
_ERR_SYNC_CONNECTION = "api:sync_connection"
_ERR_ASSET_UPLOAD = "api:asset_upload"
_ERR_ASSET_INSERT = "api:asset_insert"
_ERR_MARKDOWN_CONFLICT = "validation:markdown_conflict"
_ERR_FILE_READ = "api:file_read"


def tool_error(code: str, message: str) -> ValueError:
    """创建一个附带遥测 error_code 的 ValueError。"""
    exc = ValueError(message)
    exc.error_code = code  # type: ignore[attr-defined]
    return exc


def normalize_new_document_markdown(title: str, markdown: str) -> str:
    """Remove the first H1 line if it duplicates the document title."""
    lines = markdown.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and stripped[2:].strip() == title.strip():
            del lines[i]
            return "\n".join(lines)
        # Stop at first non-empty line — only strip immediate duplicate H1
        break
    return markdown


def read_markdown_file(path: str) -> str:
    """Read a local Markdown file, decoding utf-8 -> gbk -> gb18030 in order."""
    file_path = Path(path)
    try:
        data = file_path.read_bytes()
    except OSError as exc:
        raise tool_error(_ERR_FILE_READ, f"无法读取文件：{path}（{exc}）") from exc
    for encoding in ("utf-8", "gbk", "gb18030"):
        try:
            text = data.decode(encoding)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise tool_error(_ERR_FILE_READ, f"无法解码文件：{path}（已尝试 utf-8 / gbk / gb18030）")


def resolve_markdown_input(args: dict[str, Any], require: bool) -> str:
    """Resolve markdown content from `markdown` or `markdown_file` (mutually exclusive)."""
    markdown = args.get("markdown")
    markdown_file = args.get("markdown_file")
    has_markdown = markdown is not None and str(markdown) != ""
    has_file = markdown_file is not None and str(markdown_file).strip() != ""
    if has_markdown and has_file:
        raise tool_error(_ERR_MARKDOWN_CONFLICT, "markdown 与 markdown_file 只能填写一个，请二选一。")
    if has_file:
        return read_markdown_file(str(markdown_file).strip())
    if has_markdown:
        return str(markdown)
    if require:
        raise tool_error(_ERR_MISSING_PARAM, "需要提供 markdown 或 markdown_file（二者填一个）。")
    return ""


SKIP_BLOCK_TYPES = frozenset({"d"})
LEGACY_SKIP_BLOCK_TYPES = frozenset({"l", "d"})
SUBTREE_MARKDOWN_BLOCK_TYPES = frozenset({"i", "l", "t"})
LEGACY_SUBTREE_MARKDOWN_BLOCK_TYPES = frozenset({"i", "t"})
COMMENT_ONLY_BLOCK_TYPES = frozenset({"s"})
CHILD_TRAVERSAL_BLOCK_TYPES = frozenset({"h", "l", "s"})
DATABASE_BLOCK_TYPES = frozenset({"av"})
REPLACE_REFUSED_SEMANTIC_TYPES = frozenset({
    "attachment",
    "database",
    "superblock",
    "html",
    "iframe",
    "video",
    "audio",
    "widget",
})


import re as _re

_AV_ID_PATTERN = _re.compile(r'data-av-id="([^"]+)"')
_IAL_ATTR_PATTERN = _re.compile(r'(\w[\w-]*)\s*=\s*"([^"]*)"')


def _parse_ial_attrs(ial: str) -> dict[str, str]:
    """Parse SiYuan IAL into custom attrs, excluding id and updated (managed by kernel)."""
    attrs: dict[str, str] = {}
    for m in _IAL_ATTR_PATTERN.finditer(ial):
        key = m.group(1)
        if key in ("id", "updated"):
            continue
        attrs[key] = m.group(2)
    return attrs


def _extract_av_id(block_md: str) -> str:
    m = _AV_ID_PATTERN.search(block_md) if block_md else None
    return m.group(1) if m else ""


def _render_av_cell(value: dict[str, Any], field_type: str) -> str:
    if field_type == "block":
        block = value.get("block")
        if isinstance(block, dict):
            return str(block.get("content", ""))
        return ""
    if field_type == "select":
        mselect = value.get("mSelect")
        if isinstance(mselect, list) and mselect:
            return ", ".join(str(s.get("content", "")) for s in mselect)
        return ""
    block = value.get("block")
    if isinstance(block, dict):
        val = block.get("content")
        if val is not None:
            return str(val)
    content = value.get("content")
    if content is not None:
        return str(content)
    return ""


def _render_av_as_table(av_data: dict[str, Any], block_id: str, include_block_ids: bool) -> str:
    if not av_data:
        return ""
    key_values = av_data.get("keyValues")
    if not key_values:
        return ""
    key_ids = av_data.get("keyIDs", [])
    fields: list[dict[str, Any]] = []
    kv_by_key_id: dict[str, dict[str, Any]] = {
        kv["key"]["id"]: kv for kv in key_values if kv.get("key", {}).get("id")
    }
    if key_ids and kv_by_key_id:
        for kid in key_ids:
            if kid in kv_by_key_id:
                fields.append(kv_by_key_id[kid])
    if not fields:
        fields = list(key_values)
    row_count = max((len(f.get("values", [])) for f in fields), default=0)
    if row_count == 0:
        return ""
    headers = [f["key"]["name"] for f in fields]
    field_types = [f["key"]["type"] for f in fields]
    rows: list[list[str]] = []
    for i in range(row_count):
        row: list[str] = []
        for f, ftype in zip(fields, field_types):
            values = f.get("values", [])
            if i < len(values):
                row.append(_render_av_cell(values[i], ftype))
            else:
                row.append("")
        rows.append(row)
    lines = ["|" + "|".join(headers) + "|"]
    lines.append("|" + "|".join(" --- " for _ in headers) + "|")
    for row in rows:
        lines.append("|" + "|".join(row) + "|")
    table_md = "\n".join(lines)
    av_id = av_data.get("id", "")
    annotation = "> 此表格为数据库（属性视图），只读。如需补充数据，请在本块下方追加新表格或说明。\n\n"
    if include_block_ids:
        annotation = f"<!-- siyuan:block id={block_id} type=av -->\n" + annotation
    return annotation + table_md


def block_field(block: dict[str, Any], *names: str) -> str:
    for name in names:
        value = block.get(name)
        if value is not None:
            return str(value)
    return ""


def block_sort_key(block: dict[str, Any]) -> tuple[int, str]:
    raw = block.get("sort", 0)
    try:
        sort = int(raw)
    except (TypeError, ValueError):
        sort = 0
    return (sort, block_field(block, "id"))


def render_block_with_id(block: dict[str, Any]) -> str:
    block_type = block_field(block, "type")
    block_id = block_field(block, "id")

    if not block_id or block_type in LEGACY_SKIP_BLOCK_TYPES:
        return ""

    subtype = block_field(block, "subtype", "subType")
    subtype_str = f" subtype={subtype}" if subtype else ""
    comment = f"<!-- siyuan:block id={block_id} type={block_type}{subtype_str} -->"

    if block_type in COMMENT_ONLY_BLOCK_TYPES:
        return comment

    block_md = block_field(block, "markdown")
    if not block_md.strip():
        return ""

    return f"{comment}\n{block_md}"


def build_markdown_from_blocks(blocks: list[dict[str, Any]], root_id: str | None = None) -> str:
    """Build markdown from block records, each prefixed with its block ID comment.

    When root_id is provided, traverse parent_id + sort as a block tree instead of
    treating sort as a document-global order.
    """
    if not root_id:
        return "\n\n".join(rendered for block in blocks if (rendered := render_block_with_id(block)))

    children: dict[str, list[dict[str, Any]]] = {}
    for block in blocks:
        parent_id = block_field(block, "parent_id", "parentID")
        children.setdefault(parent_id, []).append(block)

    for child_blocks in children.values():
        child_blocks.sort(key=block_sort_key)

    parts: list[str] = []
    visited: set[str] = set()

    def mark_descendants_visited(parent_id: str) -> None:
        for child in children.get(parent_id, []):
            child_id = block_field(child, "id")
            if not child_id or child_id in visited:
                continue
            visited.add(child_id)
            mark_descendants_visited(child_id)

    def visit(block: dict[str, Any]) -> None:
        block_id = block_field(block, "id")
        if not block_id or block_id in visited:
            return
        visited.add(block_id)

        rendered = render_block_with_id(block)
        if rendered:
            parts.append(rendered)

        block_type = block_field(block, "type")
        if block_type in LEGACY_SUBTREE_MARKDOWN_BLOCK_TYPES:
            mark_descendants_visited(block_id)
            return

        for child in children.get(block_id, []):
            visit(child)

    for child in children.get(root_id, []):
        visit(child)

    for block in sorted(blocks, key=lambda item: (block_field(item, "parent_id", "parentID"), *block_sort_key(item))):
        visit(block)

    return "\n\n".join(parts)


def build_markdown_from_child_blocks(client: Any, root_id: str) -> str:
    """Build a block-ID diagnostic view using SiYuan's child-block order."""
    parts: list[str] = []
    visited: set[str] = set()

    def visit(block: dict[str, Any]) -> None:
        block_id = block_field(block, "id")
        if not block_id or block_id in visited:
            return
        visited.add(block_id)

        rendered = render_block_with_id(block)
        if rendered:
            parts.append(rendered)

        block_type = block_field(block, "type")
        if block_type in LEGACY_SUBTREE_MARKDOWN_BLOCK_TYPES:
            return
        if block_type not in CHILD_TRAVERSAL_BLOCK_TYPES:
            return

        for child in client.get_child_blocks(block_id):
            visit(child)

    for child in client.get_child_blocks(root_id):
        visit(child)

    return "\n\n".join(parts)


# ── Block Window data model and helpers ──────────────────────────────

DEFAULT_BLOCK_LIMIT = 200
MIN_BLOCK_LIMIT = 1
MAX_BLOCK_LIMIT = 1000
DEFAULT_TOKEN_BUDGET = 50000
MIN_TOKEN_BUDGET = 1000
MAX_TOKEN_BUDGET = 200000
WINDOW_PREVIEW_INTERVAL = 50
WINDOW_PREVIEW_MIN_HEADINGS = 5
WINDOW_PREVIEW_MIN_BLOCKS = 100
WINDOW_PREVIEW_PREFIX_LEN = 80


@dataclass
class DisplayBlock:
    index: int
    id: str
    type: str
    subtype: str
    markdown: str
    estimated_tokens: int
    is_heading: bool = False
    heading_level: int | None = None
    heading_text: str = ""
    source_markdown: str = ""


@dataclass
class CreateTarget:
    notebook_id: str
    notebook_name: str
    internal_path: str
    display_path: str
    existing_docs: list[dict[str, Any]]


@dataclass
class PostWriteSyncStatus:
    ok: bool
    detail: str


@dataclass(frozen=True)
class AssetInsertionItem:
    local_path: str
    basename: str
    kind: str
    name: str
    title: str
    size_bytes: int | None


def _single_line(value: Any) -> str:
    return " ".join(str(value or "").splitlines()).strip()


def _asset_default_name(path: Path, kind: str) -> str:
    if kind == "image":
        return path.stem
    return path.name


def _escape_markdown_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _escape_markdown_title(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _markdown_destination(value: str) -> str:
    if any(char.isspace() for char in value) or any(char in value for char in ("(", ")")):
        return f"<{value.replace('<', '%3C').replace('>', '%3E')}>"
    return value


def render_asset_markdown(item: AssetInsertionItem, resolved_path: str) -> str:
    label = _escape_markdown_label(item.name)
    destination = _markdown_destination(resolved_path)
    title = f' "{_escape_markdown_title(item.title)}"' if item.title else ""
    prefix = "!" if item.kind == "image" else ""
    return f"{prefix}[{label}]({destination}{title})"


def preflight_asset_items(raw_assets: Any) -> list[AssetInsertionItem]:
    if not isinstance(raw_assets, list) or not raw_assets:
        raise tool_error(_ERR_WRONG_SHAPE, "action=insert_assets 需要非空 assets 数组。")

    items: list[AssetInsertionItem] = []
    basename_sources: dict[str, list[str]] = {}
    for index, raw_item in enumerate(raw_assets, start=1):
        if not isinstance(raw_item, dict):
            raise tool_error(_ERR_INVALID_TYPE, f"assets[{index}] 必须是对象。")
        for field in ("local_path", "name", "title"):
            if raw_item.get(field) is not None and not isinstance(raw_item.get(field), str):
                raise tool_error(_ERR_INVALID_TYPE, f"assets[{index}].{field} 必须是字符串。")
        local_path = str(raw_item.get("local_path") or "").strip()
        if not local_path:
            raise tool_error(_ERR_MISSING_PARAM, f"assets[{index}].local_path 是必填的。")
        path = Path(local_path)
        if not path.is_absolute():
            raise tool_error(
                _ERR_INVALID_TYPE,
                f"assets[{index}].local_path 必须是当前 MCP 所在电脑上的绝对路径：{local_path}",
            )
        if not path.exists():
            raise tool_error(_ERR_MISSING_PARAM, f"本地路径不存在：{local_path}")
        if path.is_dir():
            kind = "directory"
            size_bytes = None
        elif path.is_file():
            kind = "image" if path.suffix.casefold() in SIYUAN_IMAGE_EXTENSIONS else "file"
            size_bytes = path.stat().st_size
        else:
            raise tool_error(_ERR_WRONG_TARGET, f"本地路径既不是普通文件也不是文件夹：{local_path}")

        basename = path.name
        if not basename:
            raise tool_error(_ERR_WRONG_TARGET, f"无法从本地路径取得文件名或文件夹名：{local_path}")
        name = _single_line(raw_item.get("name")) or _asset_default_name(path, kind)
        title = _single_line(raw_item.get("title"))
        item = AssetInsertionItem(
            local_path=local_path,
            basename=basename,
            kind=kind,
            name=name,
            title=title,
            size_bytes=size_bytes,
        )
        items.append(item)
        basename_sources.setdefault(basename.casefold(), []).append(local_path)

    duplicates = [sources for sources in basename_sources.values() if len(sources) > 1]
    if duplicates:
        lines = [
            "同一批次存在重名文件或文件夹，思源返回的 succMap 无法可靠区分它们。",
            "请把以下重名项目拆成不同调用：",
        ]
        for sources in duplicates:
            lines.append("- " + "；".join(sources))
        raise tool_error(_ERR_DUPLICATE_ASSET_NAME, "\n".join(lines))
    return items


def resolve_uploaded_asset_paths(
    items: list[AssetInsertionItem],
    succ_map: dict[str, str],
) -> list[str]:
    casefolded = {str(key).casefold(): str(value) for key, value in succ_map.items()}
    resolved: list[str] = []
    for item in items:
        candidates = (item.basename, item.local_path)
        value = next((succ_map[key] for key in candidates if key in succ_map), "")
        if not value:
            value = next((casefolded[key.casefold()] for key in candidates if key.casefold() in casefolded), "")
        if not value:
            raise tool_error(
                _ERR_ASSET_UPLOAD,
                f"思源未返回 `{item.basename}` 的资源路径。已返回键：{', '.join(sorted(succ_map)) or '(无)'}",
            )
        resolved.append(str(value))
    return resolved


def semantic_block_type(raw_type: str, subtype: str, markdown: str) -> str:
    if raw_type == "p" and re.search(r"!?\[[^\]]+\]\(assets/[^)]+\)", markdown):
        return "attachment"
    return {
        "h": "heading",
        "p": "paragraph",
        "l": "list",
        "i": "list_item",
        "t": "table",
        "c": "code",
        "s": "superblock",
        "av": "database",
        "b": "blockquote",
        "m": "math",
        "html": "html",
        "iframe": "iframe",
        "video": "video",
        "audio": "audio",
        "widget": "widget",
        "tb": "thematic_break",
    }.get(raw_type, raw_type or "unknown")


def list_kind(subtype: str) -> str:
    return {"o": "ordered", "u": "unordered", "t": "task"}.get(subtype, subtype)


def code_language(markdown: str) -> str:
    first = markdown.strip().splitlines()[0] if markdown.strip() else ""
    if first.startswith("```"):
        return first[3:].strip()
    return ""


def block_metadata_line(index: int, block_id: str, raw_type: str, subtype: str, markdown: str) -> str:
    semantic_type = semantic_block_type(raw_type, subtype, markdown)
    parts = [f"[{index}]", f"id={block_id}", f"type={semantic_type}"]
    if semantic_type == "code":
        lang = code_language(markdown)
        if lang:
            parts.append(f"language={lang}")
    elif semantic_type == "database":
        parts.append("readonly=true")
    return " ".join(parts)


def display_block_source(block: DisplayBlock) -> str:
    return block.source_markdown if block.source_markdown else block.markdown


def display_block_semantic_type(block: DisplayBlock) -> str:
    return semantic_block_type(block.type, block.subtype, display_block_source(block))


def split_markdown_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in stripped:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    if escaped:
        buf.append("\\")
    cells.append("".join(buf).strip())
    return cells


def escape_markdown_table_cell(value: Any) -> str:
    return str(value).replace("\n", "<br>").replace("|", "\\|")


def render_markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    width = len(headers)
    normalized_rows = [(row + [""] * width)[:width] for row in rows]
    lines = [
        "| " + " | ".join(escape_markdown_table_cell(header) for header in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in normalized_rows:
        lines.append("| " + " | ".join(escape_markdown_table_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


def parse_markdown_table(markdown: str) -> tuple[list[str], list[list[str]]]:
    lines = [line.strip() for line in markdown.strip().splitlines() if line.strip()]
    if len(lines) < 2 or "|" not in lines[0] or "|" not in lines[1]:
        raise tool_error(_ERR_INVALID_TABLE, "目标块不是可解析的 Markdown 表格。请重新引用阅读，确认目标块 type=table。")
    headers = split_markdown_table_row(lines[0])
    separator = split_markdown_table_row(lines[1])
    if not headers or len(separator) != len(headers):
        raise tool_error(_ERR_INVALID_TABLE, "表格表头或分隔行格式不完整，暂不支持 table_edit。")
    rows = [split_markdown_table_row(line) for line in lines[2:]]
    return headers, [(row + [""] * len(headers))[:len(headers)] for row in rows]


def render_table_coordinate_view(markdown: str) -> str:
    headers, rows = parse_markdown_table(markdown)
    lines = [
        "| row_index | " + " | ".join(f"col {i}" for i in range(1, len(headers) + 1)) + " |",
        "| row 0 | " + " | ".join(escape_markdown_table_cell(header) for header in headers) + " |",
    ]
    for row_number, row in enumerate(rows, start=1):
        normalized = (row + [""] * len(headers))[:len(headers)]
        lines.append(
            f"| row {row_number} | "
            + " | ".join(escape_markdown_table_cell(cell) for cell in normalized)
            + " |"
        )
    return "\n".join(lines)


def table_column_index(headers: list[str], edit: dict[str, Any]) -> int:
    if edit.get("column_index") is not None:
        index = int(edit["column_index"]) - 1
        if index < 0 or index >= len(headers):
            raise tool_error(_ERR_OUT_OF_RANGE, f"column_index 超出范围：当前表格共有 {len(headers)} 列。")
        return index
    column = str(edit.get("column") or "").strip()
    if not column:
        raise tool_error(_ERR_MISSING_PARAM, "table_edit.set_cell 需要 column 或 column_index。")
    matches = [i for i, header in enumerate(headers) if header == column]
    if not matches:
        raise tool_error(_ERR_NOT_FOUND, f"表格中未找到列：{column}。请重新引用阅读确认列名，或使用 column_index。")
    if len(matches) > 1:
        raise tool_error(_ERR_AMBIGUOUS, f"列名存在重复：{column}。请改用 column_index。")
    return matches[0]


def table_position(edit: dict[str, Any]) -> str:
    position = str(edit.get("position") or "").strip().lower()
    if position not in {"before", "after"}:
        raise tool_error(_ERR_INVALID_ENUM, "table_edit.position 只支持 before 或 after。")
    return position


def table_row_values(headers: list[str], values: Any) -> list[str]:
    if isinstance(values, dict):
        return [str(values.get(header, "")) for header in headers]
    if isinstance(values, list):
        return ([str(value) for value in values] + [""] * len(headers))[:len(headers)]
    raise tool_error(_ERR_INVALID_TYPE, "insert_row 需要 values，格式为按列顺序排列的数组，或按表头取值的对象。")


def apply_table_cell_edit(headers: list[str], rows: list[list[str]], cell: dict[str, Any]) -> None:
    if cell.get("row") is None:
        raise tool_error(_ERR_MISSING_PARAM, "set_cell 需要 row。row=0 表示表头，row>=1 表示数据行。")
    row_number = int(cell["row"])
    col_index = table_column_index(headers, cell)
    expected = cell.get("expected_old_value")

    if row_number == 0:
        current = headers[col_index]
        if expected is not None and current != str(expected):
            raise tool_error(_ERR_STALE_CELL_VALUE,
                f"表头单元格旧值校验失败：当前值为 `{current}`，"
                f"但 expected_old_value 为 `{expected}`。请重新引用阅读后再编辑。"
            )
        headers[col_index] = str(cell.get("value") or "")
        return

    row_index = row_number - 1
    if row_index < 0 or row_index >= len(rows):
        raise tool_error(_ERR_OUT_OF_RANGE, f"row 超出范围。当前表格有 {len(rows)} 行数据，row=0 表示表头。")
    current = rows[row_index][col_index]
    if expected is not None and current != str(expected):
        raise tool_error(_ERR_STALE_CELL_VALUE,
            f"单元格旧值校验失败：当前值为 `{current}`，"
            f"但 expected_old_value 为 `{expected}`。请重新引用阅读后再编辑。"
        )
    rows[row_index][col_index] = str(cell.get("value") or "")


def apply_table_edit(markdown: str, edit: dict[str, Any]) -> str:
    headers, rows = parse_markdown_table(markdown)
    operation = str(edit.get("operation") or "").strip()
    legacy_insert_map = {
        "insert_row_before": ("insert_row", "before"),
        "insert_row_after": ("insert_row", "after"),
    }
    if operation in legacy_insert_map:
        operation, default_position = legacy_insert_map[operation]
        edit = {**edit, "operation": operation, "position": edit.get("position") or default_position}
    if operation not in {"set_cell", "insert_row", "delete_row", "insert_column", "delete_column"}:
        raise tool_error(_ERR_INVALID_ENUM, "table_edit.operation 只支持 set_cell、insert_row、delete_row、insert_column、delete_column。")

    if operation == "set_cell":
        cells = edit.get("cells")
        if cells is not None:
            if not isinstance(cells, list) or not cells:
                raise tool_error(_ERR_INVALID_TYPE, "set_cell.cells 必须是非空数组。")
            for cell in cells:
                if not isinstance(cell, dict):
                    raise tool_error(_ERR_INVALID_TYPE, "set_cell.cells 中的每一项都必须是对象。")
                apply_table_cell_edit(headers, rows, cell)
        else:
            cell = edit.get("cell")
            if cell is None:
                cell = edit
            if not isinstance(cell, dict):
                raise tool_error(_ERR_INVALID_TYPE, "set_cell 需要 cell 对象或 cells 数组。")
            apply_table_cell_edit(headers, rows, cell)
    elif operation == "insert_row":
        if edit.get("row") is None:
            raise tool_error(_ERR_MISSING_PARAM, "insert_row 需要 row。row=0 表示表头，row>=1 表示数据行。")
        row_number = int(edit["row"])
        position = table_position(edit)
        if row_number < 0 or row_number > len(rows):
            raise tool_error(_ERR_OUT_OF_RANGE, f"row 超出范围。当前表格有 {len(rows)} 行数据，row=0 表示表头。")
        if row_number == 0 and position == "before":
            raise tool_error(_ERR_OPERATION_ORDER, "不能在表头前插入数据行。请使用 row=0, position=after 或指定数据行。")
        new_row = table_row_values(headers, edit.get("values"))
        insert_at = 0 if row_number == 0 else row_number - 1
        if position == "after" and row_number > 0:
            insert_at += 1
        rows.insert(insert_at, new_row)
    elif operation == "delete_row":
        row_arg = edit.get("row")
        if row_arg is None:
            raise tool_error(_ERR_MISSING_PARAM, "delete_row 需要 row。row>=1 表示数据行，不能删除表头。")
        row_index = int(row_arg) - 1
        if row_index < 0 or row_index >= len(rows):
            raise tool_error(_ERR_OUT_OF_RANGE, f"row 超出范围。当前表格有 {len(rows)} 行数据。")
        rows.pop(row_index)
    elif operation == "insert_column":
        col_index = table_column_index(headers, edit)
        position = table_position(edit)
        values = edit.get("values")
        if not isinstance(values, list):
            raise tool_error(_ERR_INVALID_TYPE, "insert_column 需要 values 数组，values[0] 是表头，其后是数据行。")
        if len(values) > len(rows) + 1:
            raise tool_error(_ERR_OUT_OF_RANGE, f"insert_column.values 过长。当前表格需要最多 {len(rows) + 1} 个值（含表头）。")
        normalized = [str(value) for value in values] + [""] * (len(rows) + 1 - len(values))
        insert_at = col_index if position == "before" else col_index + 1
        headers.insert(insert_at, normalized[0])
        for row, value in zip(rows, normalized[1:]):
            row.insert(insert_at, value)
    elif operation == "delete_column":
        if len(headers) <= 1:
            raise tool_error(_ERR_OPERATION_ORDER, "不能删除最后一列。")
        col_index = table_column_index(headers, edit)
        headers.pop(col_index)
        for row in rows:
            row.pop(col_index)

    return render_markdown_table(headers, rows)


def display_document_path(doc: dict[str, Any]) -> str:
    hpath = str(doc.get("hpath") or doc.get("title") or doc.get("id"))
    notebook_name = str(doc.get("notebook_name") or "").strip()
    if not hpath.startswith("/"):
        hpath = "/" + hpath
    if notebook_name and not hpath.startswith(f"/{notebook_name}/") and hpath != f"/{notebook_name}":
        return f"/{notebook_name}{hpath}"
    return hpath


def normalize_display_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    return "/" + text.strip("/")


def _notebook_by_id(notebooks: list[dict[str, Any]], notebook_id: str) -> dict[str, Any] | None:
    return next((nb for nb in notebooks if str(nb.get("id", "")) == notebook_id), None)


def _notebook_name_matches(notebooks: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    folded = name.casefold()
    return [nb for nb in notebooks if str(nb.get("name", "")).casefold() == folded]


def _existing_docs_at_path(docs: list[dict[str, Any]], notebook_id: str, internal_path: str) -> list[dict[str, Any]]:
    wanted = normalize_display_path(internal_path).strip("/").casefold()
    return [
        doc for doc in docs
        if str(doc.get("notebook_id", "")) == notebook_id
        and normalize_display_path(str(doc.get("hpath", ""))).strip("/").casefold() == wanted
    ]


def resolve_create_target(
    args: dict[str, Any],
    notebooks: list[dict[str, Any]],
    docs: list[dict[str, Any]],
    title: str,
) -> CreateTarget:
    raw_path = str(args.get("path") or "").strip()
    notebook_id_arg = str(args.get("notebook_id") or "").strip()
    path = normalize_display_path(raw_path)

    if not path:
        if not notebook_id_arg:
            raise tool_error(_ERR_MISSING_PARAM,
                "siyuan_create 优先使用完整路径 path=/Notebook/Folder/Doc。"
                "如果不传 path，则必须提供 notebook_id 和笔记本内路径。"
            )
        nb = _notebook_by_id(notebooks, notebook_id_arg)
        if nb is None:
            raise tool_error(
                _ERR_NB_NOT_FOUND,
                f"笔记本 {notebook_id_arg} 不可见或不存在，可能已被隐私规则隐藏。"
                "如果需要新建笔记本，请先调用 "
                '`siyuan_doc_manage(action="create_notebook", notebook_name="<笔记本名称>", confirmed=true)`。',
            )
        internal_path = f"/{title}"
    else:
        parts = path.strip("/").split("/", 1)
        first = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        name_matches = _notebook_name_matches(notebooks, first)

        if len(name_matches) > 1 and not notebook_id_arg:
            choices = "\n".join(f"- `{nb.get('id', '')}` {nb.get('name', '')}" for nb in name_matches)
            raise tool_error(_ERR_AMBIGUOUS,
                "目标笔记本名称存在歧义。请改用 notebook_id + 笔记本内路径，例如 "
                "`notebook_id=<目标笔记本ID>, path=/Folder/Doc`。\n"
                + choices
            )

        if name_matches:
            if notebook_id_arg:
                nb = next((item for item in name_matches if str(item.get("id", "")) == notebook_id_arg), None)
                if nb is None:
                    raise tool_error(_ERR_MISMATCH, "path 中的笔记本名称与 notebook_id 不匹配。")
            else:
                nb = name_matches[0]
            internal_path = normalize_display_path(rest or title)
        else:
            if not notebook_id_arg:
                raise tool_error(_ERR_NB_NOT_FOUND,
                    "path 应使用完整可读路径 /Notebook/Folder/Doc。"
                    "未匹配到路径第一段对应的可见笔记本。"
                    "如果该笔记本尚未创建，请先调用 "
                    f'`siyuan_doc_manage(action="create_notebook", notebook_name="{first}", confirmed=true)`；'
                    "如需使用已有笔记本的内部路径，请同时提供 notebook_id。"
                )
            nb = _notebook_by_id(notebooks, notebook_id_arg)
            if nb is None:
                raise tool_error(
                    _ERR_NB_NOT_FOUND,
                    f"笔记本 {notebook_id_arg} 不可见或不存在，可能已被隐私规则隐藏。"
                    "如果需要新建笔记本，请先调用 "
                    '`siyuan_doc_manage(action="create_notebook", notebook_name="<笔记本名称>", confirmed=true)`。',
                )
            internal_path = path

    notebook_id = str(nb.get("id", ""))
    notebook_name = str(nb.get("name", notebook_id))
    internal_path = normalize_display_path(internal_path)
    display_path = normalize_display_path(f"{notebook_name}/{internal_path.strip('/')}")
    existing_docs = _existing_docs_at_path(docs, notebook_id, internal_path)
    return CreateTarget(
        notebook_id=notebook_id,
        notebook_name=notebook_name,
        internal_path=internal_path,
        display_path=display_path,
        existing_docs=existing_docs,
    )


def direct_child_key(parent_path: str, document_path: str) -> str | None:
    parent = normalize_display_path(parent_path)
    doc_path = normalize_display_path(document_path)
    if not parent:
        return None
    if doc_path == parent or not doc_path.startswith(parent + "/"):
        return None
    remainder = doc_path[len(parent):].strip("/")
    if not remainder:
        return None
    return remainder.split("/", 1)[0]


def descendant_count(doc: dict[str, Any], docs: list[dict[str, Any]]) -> int:
    path = display_document_path(doc).rstrip("/")
    return sum(
        1 for item in docs
        if display_document_path(item).startswith(path + "/")
    )


def document_subtree(doc: dict[str, Any], docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    doc_id = str(doc.get("id") or "")
    notebook_id = str(doc.get("notebook_id") or "")
    hpath = normalize_display_path(str(doc.get("hpath") or "")).rstrip("/")
    result = []
    for item in docs:
        if str(item.get("id") or "") == doc_id:
            result.append(item)
            continue
        if str(item.get("notebook_id") or "") != notebook_id:
            continue
        item_hpath = normalize_display_path(str(item.get("hpath") or "")).rstrip("/")
        if hpath and item_hpath.startswith(hpath + "/"):
            result.append(item)
    return result


def expand_deleted_block_ids(blocks: list[dict[str, Any]], root_ids: set[str]) -> set[str]:
    """Include every descendant that disappears when one of *root_ids* is deleted."""
    children: dict[str, list[str]] = {}
    for block in blocks:
        block_id = str(block.get("id") or "").strip()
        parent_id = str(block.get("parent_id") or "").strip()
        if block_id and parent_id:
            children.setdefault(parent_id, []).append(block_id)
    deleted = {block_id for block_id in root_ids if block_id}
    pending = list(deleted)
    while pending:
        parent_id = pending.pop()
        for child_id in children.get(parent_id, []):
            if child_id not in deleted:
                deleted.add(child_id)
                pending.append(child_id)
    return deleted


def reference_excerpt(row: dict[str, Any], limit: int = 180) -> str:
    text = str(row.get("content") or row.get("markdown") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return "（引用块内容为空或无法读取）"
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def reference_block_markdown(row: dict[str, Any]) -> str:
    text = str(row.get("markdown") or row.get("content") or "").strip()
    if not text:
        return "（引用块内容为空或无法读取）"
    if len(text) > MAX_REFERENCE_BLOCK_CHARACTERS:
        return (
            text[:MAX_REFERENCE_BLOCK_CHARACTERS].rstrip()
            + "\n\n（内容超过 2000 字符，已截断；更多内容请查看原文档。）"
        )
    return text


def parse_reference_limit(value: Any) -> int | None:
    if value is None:
        return DEFAULT_REFERENCE_DOCUMENT_LIMIT
    if isinstance(value, str) and value.strip().casefold() == "none":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise tool_error(_ERR_INVALID_TYPE, 'limit 必须是大于等于 1 的整数或字符串 "none"。')
    if value < 1:
        raise tool_error(_ERR_OUT_OF_RANGE, 'limit 最小为 1；使用 "none" 可查看全部结果。')
    return value


def parent_display_path(document_path: str) -> str:
    parts = normalize_display_path(document_path).strip("/").split("/")
    if len(parts) <= 1:
        return ""
    return "/" + "/".join(parts[:-1])


def notebook_permission_probe(notebook: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "",
        "notebook_id": str(notebook.get("id", "")),
        "notebook_name": str(notebook.get("name", "")),
        "hpath": "/__siyuan_bridge_permission_probe__",
    }


def format_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def estimate_token_count(text: str) -> int:
    """Heuristic token estimator. CJK ~1.0 tok/char, Latin ~1.3 tok/word, digits ~0.8 tok/item, punctuation ~0.4 tok/char."""
    if not text:
        return 0
    cjk = 0
    latin_words = 0
    digits = 0
    punct = 0
    buf = ""
    for ch in text:
        cp = ord(ch)
        if cp >= 0x4E00 and cp <= 0x9FFF or cp >= 0x3400 and cp <= 0x4DBF or cp >= 0x20000 and cp <= 0x2A6DF:
            if buf:
                latin_words += len(buf.split())
                buf = ""
            cjk += 1
        elif ch.isdigit():
            if buf:
                latin_words += len(buf.split())
                buf = ""
            digits += 1
        elif ch.isalpha():
            buf += ch
        else:
            if buf:
                latin_words += len(buf.split())
                buf = ""
            if not ch.isspace() and not cp >= 0x4E00:
                punct += 1
    if buf:
        latin_words += len(buf.split())
    return int(cjk * 1.0 + latin_words * 1.3 + digits * 0.8 + punct * 0.4)


def build_display_blocks(client: Any, root_id: str, *, include_block_ids: bool = False) -> list[DisplayBlock]:
    """Build ordered list of DisplayBlock using SiYuan's getChildBlocks API."""
    blocks: list[DisplayBlock] = []
    visited: set[str] = set()

    def visit(block: dict[str, Any]) -> None:
        block_id = block_field(block, "id")
        if not block_id or block_id in visited:
            return
        visited.add(block_id)

        block_type = block_field(block, "type")
        # Skip document roots; list containers are rendered as one display block.
        if block_type in SKIP_BLOCK_TYPES:
            if block_type in CHILD_TRAVERSAL_BLOCK_TYPES:
                for child in client.get_child_blocks(block_id):
                    visit(child)
            return

        # Database/attribute view blocks: render via av API, not raw markdown
        if block_type in DATABASE_BLOCK_TYPES:
            block_md = block_field(block, "markdown")
            av_id = _extract_av_id(block_md)
            if av_id:
                av_data = client.get_attribute_view(av_id)
                display_md = _render_av_as_table(av_data, block_id, False) if av_data else ""
            else:
                display_md = ""
            if not display_md:
                display_md = "> 数据库数据获取失败"
            if include_block_ids:
                display_md = f"{block_metadata_line(len(blocks) + 1, block_id, block_type, '', block_md)}\n{display_md}"
            estimated_tokens = estimate_token_count(display_md)
            blocks.append(DisplayBlock(
                index=len(blocks) + 1,
                id=block_id,
                type=block_type,
                subtype="",
                markdown=display_md,
                estimated_tokens=estimated_tokens,
                is_heading=False,
                heading_level=None,
                heading_text="",
                source_markdown=block_md,
            ))
            return

        subtype = block_field(block, "subtype", "subType")
        block_md = block_field(block, "markdown")

        if block_type == "l" and not block_md.strip():
            for child in client.get_child_blocks(block_id):
                visit(child)
            return

        if not block_md.strip() and block_type not in COMMENT_ONLY_BLOCK_TYPES:
            if block_type in CHILD_TRAVERSAL_BLOCK_TYPES:
                for child in client.get_child_blocks(block_id):
                    visit(child)
            return

        is_heading = block_type == "h"
        heading_level = None
        heading_text = ""
        if is_heading:
            try:
                heading_level = int(subtype[1]) if subtype.startswith("h") else None
            except (ValueError, IndexError):
                heading_level = None
            heading_text = block_md.lstrip("#").strip()

        display_md = block_md
        if block_type in COMMENT_ONLY_BLOCK_TYPES:
            if include_block_ids:
                display_md = block_metadata_line(len(blocks) + 1, block_id, block_type, subtype, block_md) + "\n{{{ superblock start"
            else:
                for child in client.get_child_blocks(block_id):
                    visit(child)
                return
        elif include_block_ids and block_type == "t" and block_md.strip():
            metadata = block_metadata_line(len(blocks) + 1, block_id, block_type, subtype, block_md)
            try:
                headers, rows = parse_markdown_table(block_md)
                display_md = (
                    f"{metadata} rows={len(rows)} columns={len(headers)}\n\n"
                    f"{render_table_coordinate_view(block_md)}"
                )
            except ValueError:
                display_md = f"{metadata}\n{block_md}"
        elif include_block_ids and block_md.strip():
            metadata = block_metadata_line(len(blocks) + 1, block_id, block_type, subtype, block_md)
            display_md = f"{metadata}\n{block_md}"

        # Skip blocks with no visible content in normal mode
        if not include_block_ids and not block_md.strip():
            if block_type in CHILD_TRAVERSAL_BLOCK_TYPES:
                for child in client.get_child_blocks(block_id):
                    visit(child)
            return

        estimated_tokens = estimate_token_count(block_md)
        blocks.append(DisplayBlock(
            index=len(blocks) + 1,
            id=block_id,
            type=block_type,
            subtype=subtype,
            markdown=display_md,
            estimated_tokens=estimated_tokens,
            is_heading=is_heading,
            heading_level=heading_level,
            heading_text=heading_text,
            source_markdown=block_md,
        ))

        # List items and tables: their markdown already contains subtree content — skip children
        if block_type in SUBTREE_MARKDOWN_BLOCK_TYPES:
            return
        # Continue traversing children for headings, super blocks, list containers
        if block_type in CHILD_TRAVERSAL_BLOCK_TYPES:
            start_index = len(blocks)
            current_display_index = blocks[-1].index if blocks else 0
            for child in client.get_child_blocks(block_id):
                visit(child)
            if include_block_ids and block_type in COMMENT_ONLY_BLOCK_TYPES and blocks:
                end_marker = "}}} superblock end [" + str(current_display_index) + "]"
                target_idx = len(blocks) - 1 if len(blocks) > start_index else start_index - 1
                if 0 <= target_idx < len(blocks):
                    blocks[target_idx].markdown = f"{blocks[target_idx].markdown}\n\n{end_marker}"

    for child in client.get_child_blocks(root_id):
        visit(child)

    return blocks


def build_block_outline(display_blocks: list[DisplayBlock]) -> str:
    """Build an outline showing heading block positions."""
    headings = [b for b in display_blocks if b.is_heading]
    if not headings:
        return "## 大纲\n\n(文档无标题结构)"

    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []

    for db in headings:
        node: dict[str, Any] = {
            "text": db.heading_text,
            "level": db.heading_level or 1,
            "block_index": db.index,
            "children": [],
        }

        while stack and stack[-1][0] >= (db.heading_level or 1):
            stack.pop()

        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)

        stack.append((db.heading_level or 1, node))

    def _fmt(node: dict[str, Any], indent: int) -> list[str]:
        prefix = "  " * indent
        lines = [f"{prefix}- block {node['block_index']}: {'#' * node['level']} {node['text']}"]
        for child in node["children"]:
            lines.extend(_fmt(child, indent + 1))
        return lines

    body: list[str] = []
    for r in roots:
        body.extend(_fmt(r, 0))

    total = len(display_blocks)
    parts = [f"## 大纲 ({len(headings)} 个标题, {total} 个展示块)"]
    parts.extend(body)
    return "\n".join(parts)


def build_window_preview(display_blocks: list[DisplayBlock]) -> str:
    """Build a window preview for low-heading, high-block documents.

    Only when headings < 5 AND total blocks > 100.
    Previews every 50 blocks with a short snippet of the block text.
    """
    heading_count = sum(1 for b in display_blocks if b.is_heading)
    total = len(display_blocks)
    if heading_count >= WINDOW_PREVIEW_MIN_HEADINGS or total <= WINDOW_PREVIEW_MIN_BLOCKS:
        return ""

    lines = [
        f"本文档标题较少（{heading_count} 个），抽取每 {WINDOW_PREVIEW_INTERVAL} 个块的开头片段帮助选择阅读窗口：",
        "",
    ]
    for db in display_blocks:
        if (db.index - 1) % WINDOW_PREVIEW_INTERVAL == 0:
            text = db.markdown
            # Strip block ID comment for preview
            if text.startswith("<!-- siyuan:block"):
                text = text.split("-->", 1)[-1].strip()
            snippet = text[:WINDOW_PREVIEW_PREFIX_LEN].replace("\n", " ")
            lines.append(f"- block {db.index}: {snippet}")

    lines.append("")
    return "\n".join(lines)


def format_display_block(block: DisplayBlock) -> str:
    return block.markdown.strip()


def format_display_blocks(blocks: list[DisplayBlock]) -> str:
    if not blocks:
        return "(无)"
    return "\n\n".join(format_display_block(block) for block in blocks)


def block_range_label(blocks: list[DisplayBlock]) -> str:
    if not blocks:
        return "(无)"
    first = blocks[0]
    last = blocks[-1]
    first_label = f"[{first.index}] id={first.id} type={display_block_semantic_type(first)}"
    if len(blocks) == 1:
        return first_label
    return f"{first_label} -> [{last.index}] id={last.id} type={display_block_semantic_type(last)}"


def block_index_by_id(blocks: list[DisplayBlock], block_id: str) -> int | None:
    for index, block in enumerate(blocks):
        if block.id == block_id:
            return index
    return None


def blocks_between_anchors(
    blocks: list[DisplayBlock],
    previous_id: str | None,
    next_id: str | None,
) -> list[DisplayBlock]:
    start = 0
    if previous_id:
        previous_index = block_index_by_id(blocks, previous_id)
        if previous_index is not None:
            start = previous_index + 1
    end = len(blocks)
    if next_id:
        next_index = block_index_by_id(blocks, next_id)
        if next_index is not None:
            end = next_index
    if end < start:
        return []
    return blocks[start:end]


def markdown_has_multiple_blocks(markdown: str) -> bool:
    in_fence = False
    saw_content = False
    saw_blank_after_content = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
        if in_fence:
            if stripped:
                saw_content = True
            continue
        if not stripped:
            if saw_content:
                saw_blank_after_content = True
            continue
        if saw_blank_after_content:
            return True
        saw_content = True
    return False


def main() -> int:
    server = McpServer(Path.cwd())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = server.handle(request)
        except Exception as exc:
            response = make_error(None, -32603, str(exc))
        if response is not None:
            write_message(response)
    return 0


def _extract_tool_action(tool_name: str, args: dict[str, Any]) -> str | None:
    """Extract the sub-action from tool arguments for telemetry grouping."""
    if tool_name == "siyuan_edit":
        action = args.get("action")
        return str(action) if action else None
    if tool_name == "siyuan_create":
        if_exists = args.get("if_exists")
        return str(if_exists) if if_exists else None
    if tool_name == "siyuan_doc_manage":
        action = args.get("action")
        return str(action) if action else None
    if tool_name == "siyuan_operate":
        action = args.get("action")
        return str(action) if action else None
    return None


class McpServer:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._active_profile: Profile | None = None
        self._active_client: SiYuanClient | None = None

    def _clear_active_connection(self) -> None:
        self._active_profile = None
        self._active_client = None

    def _require_active_client(self) -> SiYuanClient:
        if self._active_client is None:
            raise SiYuanConnectionError(
                "思源桥尚未初始化。请先调用 siyuan_start，检测当前工作空间并建立连接。"
            )
        return self._active_client

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            return make_result(
                request_id,
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return make_result(request_id, {"tools": tool_specs()})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            return self.call_tool(request_id, str(name), args)
        if method == "ping":
            return make_result(request_id, {})

        return make_error(request_id, -32601, f"Unknown method: {method}")

    def call_tool(self, request_id: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tools: dict[str, Callable[[dict[str, Any]], str]] = {
            "siyuan_start": self.siyuan_start,
            "siyuan_operate": self.siyuan_operate,
            "siyuan_list": self.siyuan_list,
            "siyuan_find": self.siyuan_find,
            "siyuan_read": self.siyuan_read,
            "siyuan_create": self.siyuan_create,
            "siyuan_edit": self.siyuan_edit,
            "siyuan_doc_manage": self.siyuan_doc_manage,
            "siyuan_bridge_feedback": self.siyuan_bridge_feedback,
        }
        if name not in tools:
            return make_error(request_id, -32602, f"Unknown tool: {name}")
        if (
            name not in {"siyuan_start", "siyuan_bridge_feedback"}
            and self._active_client is None
        ):
            return make_result(
                request_id,
                {
                    "content": [{
                        "type": "text",
                        "text": (
                            "思源桥尚未初始化。请先调用 siyuan_start，"
                            "检测当前工作空间并建立连接。"
                        ),
                    }],
                    "isError": True,
                },
            )
        try:
            action = _extract_tool_action(name, args)

            if name == "siyuan_bridge_feedback":
                # Feedback does not require SiYuan to be running
                text = _with_telemetry(
                    self.root, name, action,
                    lambda: tools[name](args),
                )
            else:
                text = _with_telemetry(
                    self.root, name, action,
                    lambda: tools[name](args),
                )
            return make_result(request_id, {"content": [{"type": "text", "text": text}]})
        except SiYuanConnectionError as exc:
            reason = str(exc).strip()
            if not reason:
                reason = "无法连接到思源笔记"
            self._clear_active_connection()
            if name != "siyuan_start":
                message = (
                    f"当前思源连接已失效：{reason}\n\n"
                    "请重新调用 siyuan_start，重新检测当前工作空间并建立连接。"
                )
            else:
                message = f"思源桥启动失败：{reason}"
            return make_result(
                request_id,
                {"content": [{"type": "text", "text": message}], "isError": True},
            )
        except SiYuanApiError as exc:
            if exc.status in (401, 403) or exc.code in (401, 403):
                self._clear_active_connection()
                message = (
                    f"当前思源连接的 API Token 已失效：{exc}\n\n"
                    "请重新调用 siyuan_start，重新检测当前工作空间并建立连接。"
                )
            else:
                message = f"工具执行失败：{exc}"
            return make_result(
                request_id,
                {"content": [{"type": "text", "text": message}], "isError": True},
            )
        except (ValueError, FileNotFoundError) as exc:
            return make_result(
                request_id,
                {"content": [{"type": "text", "text": f"工具执行失败：{exc}"}], "isError": True},
            )
        except PrivacyRulesUnavailableError as exc:
            self._clear_active_connection()
            return make_result(
                request_id,
                {"content": [{"type": "text", "text": str(exc)}], "isError": True},
            )

    def _refresh_index_with_system_context(self, client: SiYuanClient) -> None:
        state = self._load_system_context(client)
        write_privacy_rules_cache(self.root, state.privacy_rules)
        refresh_index(
            client,
            self.root,
            system_notebook_id=state.notebook_id,
            privacy_rules_doc_ids=set(state.privacy_rules_doc_ids),
        )

    def _load_system_context(
        self,
        client: SiYuanClient,
        *,
        notify_missing: bool = False,
    ) -> AgentNotebookState:
        config = load_config(self.root)
        try:
            state = load_agent_notebook(
                client, self.root, config_language=config.language or None
            )
        except PrivacyRulesUnavailableError as exc:
            try:
                client.push_err_msg(str(exc), timeout=12000)
            except Exception:
                pass
            raise
        if notify_missing and state.missing_document_keys:
            labels = {
                "ai_guide": "用户个性化要求",
                "mcp_usage_guide": "MCP 使用指南",
                "workspace_index_guide": "工作空间索引创建指南",
                "workspace_index": "工作空间索引",
                "about": "关于思源桥",
            }
            missing = [labels[key] for key in state.missing_document_keys if key in labels]
            if missing:
                message = (
                    "思源桥系统文档缺失：" + "、".join(missing)
                    + "。当前仍可继续使用；禁用并重新启用思源桥插件可重新创建。"
                )
                try:
                    client.push_msg(message, timeout=10000)
                except Exception:
                    pass
        return state

    def _wait_for_hpath(self, client: SiYuanClient, doc_id: str, expected_hpath: str) -> PostWriteSyncStatus:
        expected = normalize_display_path(expected_hpath).casefold()
        deadline = time.monotonic() + POST_WRITE_SYNC_TIMEOUT
        last_seen_api = ""
        last_seen_sql = ""
        while time.monotonic() < deadline:
            try:
                current = normalize_display_path(client.get_hpath_by_id(doc_id))
            except Exception:
                current = ""
            if current:
                last_seen_api = current
            try:
                live_doc = next((doc for doc in load_live_docs(client) if str(doc.get("id", "")) == doc_id), None)
                live_hpath = normalize_display_path(str(live_doc.get("hpath", ""))) if live_doc else ""
            except Exception:
                live_hpath = ""
            if live_hpath:
                last_seen_sql = live_hpath
            if current and live_hpath and current.casefold() == expected and live_hpath.casefold() == expected:
                return PostWriteSyncStatus(True, f"路径已同步：{current}")
            time.sleep(POST_WRITE_SYNC_INTERVAL)
        if last_seen_api or last_seen_sql:
            details = []
            if last_seen_api:
                details.append(f"路径接口：{last_seen_api}")
            if last_seen_sql:
                details.append(f"索引源：{last_seen_sql}")
            return PostWriteSyncStatus(False, f"路径尚未同步到目标；{'; '.join(details)}，目标路径：{expected_hpath}")
        return PostWriteSyncStatus(False, f"路径尚未同步到目标：{expected_hpath}")

    def _wait_for_deleted_doc(self, client: SiYuanClient, doc_id: str) -> PostWriteSyncStatus:
        deadline = time.monotonic() + POST_WRITE_SYNC_TIMEOUT
        last_seen = ""
        while time.monotonic() < deadline:
            try:
                current = normalize_display_path(client.get_hpath_by_id(doc_id))
            except Exception:
                return PostWriteSyncStatus(True, "文档删除已同步")
            if not current:
                return PostWriteSyncStatus(True, "文档删除已同步")
            last_seen = current
            time.sleep(POST_WRITE_SYNC_INTERVAL)
        return PostWriteSyncStatus(False, f"删除操作尚未从路径接口确认；当前仍可见：{last_seen}")

    def siyuan_start(self, _args: dict[str, Any]) -> str:
        self._clear_active_connection()
        config = load_config(self.root)
        profile, client = detect_active_profile(config)
        version = client.version()

        # Initialize telemetry session
        set_siyuan_version(version)
        load_anonymous_id(self.root)
        ensure_session_id()

        # Read the plugin-maintained system notebook and parse privacy rules.
        state = self._load_system_context(client, notify_missing=True)
        nb_id = state.notebook_id

        # Cache privacy rules for other tools
        write_privacy_rules_cache(self.root, state.privacy_rules)

        # Clean ai_workspace (preserve README.md)
        workspace_dir = self.root / "ai_workspace"
        if workspace_dir.exists():
            for item in workspace_dir.iterdir():
                if item.name == "README.md":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # Refresh indexes using cached privacy rules
        refresh_index(
            client, self.root,
            system_notebook_id=nb_id,
            privacy_rules_doc_ids=set(state.privacy_rules_doc_ids),
        )

        overview = build_notebook_overview(self.root)
        overview = re.sub(r"^# [^\n]+\n+", "", overview).strip()
        total_ignore = len(state.privacy_rules.ignore)
        total_permissions = len(state.privacy_rules.permissions)
        nb_rules = [
            rule
            for rule in state.privacy_rules.ignore + state.privacy_rules.permissions
            if rule.get("scope") == "notebook"
        ]
        doc_rules = [
            rule
            for rule in state.privacy_rules.ignore + state.privacy_rules.permissions
            if rule.get("scope") == "document"
        ]
        privacy_status = (
            f"已正常加载（{len(nb_rules)} 条笔记本规则，{len(doc_rules)} 条文档规则）"
            if total_ignore or total_permissions
            else "已正常加载（无规则）"
        )
        parts: list[str] = [
            "# 思源桥启动包",
            "",
            (
                f"思源版本：{version}｜当前工作空间：**{profile.name}**｜"
                f"隐私规则：{privacy_status}"
            ),
            "",
            "## MCP 使用指南",
            "",
            (
                state.mcp_usage_guide_markdown.strip()
                if state.mcp_usage_guide_markdown
                else "（MCP 使用指南为空）"
            ),
            "",
            "## 用户个性化要求",
            "",
            (
                state.ai_guide_markdown.strip()
                if state.ai_guide_markdown
                else "（用户尚未填写个性化要求）"
            ),
            "",
            "## 笔记本概览和统计",
            "",
            overview,
            "",
            "## 工作空间索引",
            "",
            f"最后更新时间：{format_siyuan_updated(state.workspace_index_updated)}",
        ]
        non_privacy_missing = [
            key for key in state.missing_document_keys if key != "privacy_rules"
        ]
        if non_privacy_missing:
            labels = {
                "ai_guide": "用户个性化要求",
                "mcp_usage_guide": "MCP 使用指南",
                "workspace_index_guide": "工作空间索引创建指南",
                "workspace_index": "工作空间索引",
                "about": "关于思源桥",
            }
            missing_text = "、".join(labels.get(key, key) for key in non_privacy_missing)
            parts[4:4] = [
                "## 系统文档警告",
                "",
                (
                    f"> 以下系统文档缺失：{missing_text}。当前功能继续运行；"
                    "请提醒用户禁用并重新启用思源桥插件以重新创建。"
                ),
                "",
            ]

        age_days = workspace_index_age_days(state.workspace_index_updated)
        if state.workspace_index_is_placeholder:
            parts.extend([
                "",
                "> 用户尚未创建工作空间索引。请询问用户是否需要创建；创建方法见系统笔记本中的《工作空间索引创建指南》。",
            ])
        elif age_days is not None and age_days > 30:
            parts.extend([
                "",
                (
                    f"> 工作空间索引已经 {age_days} 天没有更新。请询问用户是否需要更新；"
                    "更新方法见系统笔记本中的《工作空间索引创建指南》。"
                ),
            ])
        parts.extend([
            "",
            (
                state.workspace_index_markdown.strip()
                if state.workspace_index_markdown
                else "（工作空间索引为空）"
            ),
            "",
        ])
        result = "\n".join(parts)
        self._active_profile = profile
        self._active_client = client
        return result

    def _refresh_safe_index(self) -> str:
        client = self._require_active_client()

        state = self._load_system_context(client)
        write_privacy_rules_cache(self.root, state.privacy_rules)

        result = refresh_index(
            client, self.root,
            system_notebook_id=state.notebook_id,
            privacy_rules_doc_ids=set(state.privacy_rules_doc_ids),
        )
        total_ignore = len(state.privacy_rules.ignore)
        total_permissions = len(state.privacy_rules.permissions)
        total_rules = total_ignore + total_permissions
        return (
            "# 索引已刷新\n\n"
            f"可见：{result.notebook_count} 个笔记本、{result.document_count} 篇文档。\n"
            f"隐私规则：{total_rules} 条隐私规则已生效。"
        )

    def siyuan_operate(self, args: dict[str, Any]) -> str:
        action = str(args.get("action") or "").strip().casefold()
        if action not in {"refresh", "sync", "check_references"}:
            raise tool_error(_ERR_INVALID_ENUM, "action 必须是 refresh、sync 或 check_references。")
        if action == "refresh":
            return self._refresh_safe_index()
        if action == "check_references":
            return self._check_references(args)

        timeout_seconds = clamp_int(args.get("timeout_seconds"), 10, 5, 120)
        client = self._require_active_client()
        try:
            client.perform_sync(timeout=float(timeout_seconds))
        except SiYuanTimeoutError as exc:
            raise tool_error(
                _ERR_SYNC_TIMEOUT,
                f"思源同步超过 {timeout_seconds} 秒仍未完成。请稍后检查同步状态；如果长期超时，请手动延长 timeout_seconds 或检查网络/同步服务。",
            ) from exc
        except SiYuanConnectionError as exc:
            raise tool_error(
                _ERR_SYNC_CONNECTION,
                f"思源同步连接失败：{exc}。请检查网络、代理或同步服务状态。",
            ) from exc
        status = client.get_sync_info()
        stat = str(status.get("stat") or "").strip()
        synced = str(status.get("synced") or "").strip()
        failed_markers = ("失败", "错误", "failed", "error", "不可用", "未启用")
        title = "# 同步失败" if any(marker in stat.casefold() for marker in failed_markers) else "# 同步已完成"
        lines = [title, "", "已调用思源内置同步。"]
        if stat:
            lines.append(f"状态：{stat}")
        if synced:
            lines.append(f"同步时间：{synced}")
        return "\n".join(lines)

    def _check_references(self, args: dict[str, Any]) -> str:
        limit = parse_reference_limit(args.get("limit"))
        client = self._require_active_client()
        target_doc = self._resolve_reference_document(args, client)
        target_doc_id = str(target_doc.get("id") or "")

        with ensure_notebooks_open(client):
            live_docs = load_live_docs(client)
            docs_by_id = {
                str(doc.get("id") or ""): doc
                for doc in live_docs
                if str(doc.get("id") or "")
            }
            live_target = docs_by_id.get(target_doc_id, target_doc)
            subtree = document_subtree(live_target, live_docs)
            if not any(str(doc.get("id") or "") == target_doc_id for doc in subtree):
                subtree.insert(0, live_target)

            target_owner: dict[str, str] = {}
            for subtree_doc in subtree:
                subtree_doc_id = str(subtree_doc.get("id") or "")
                if not subtree_doc_id:
                    continue
                target_owner[subtree_doc_id] = subtree_doc_id
                for block in client.list_document_blocks(subtree_doc_id):
                    block_id = str(block.get("id") or "")
                    if block_id:
                        target_owner[block_id] = subtree_doc_id
            references = client.list_block_references(sorted(target_owner))

        references_by_doc: dict[str, list[dict[str, Any]]] = {
            str(doc.get("id") or ""): []
            for doc in subtree
            if str(doc.get("id") or "")
        }
        for row in references:
            owner_id = target_owner.get(str(row.get("def_block_id") or ""))
            if owner_id:
                references_by_doc.setdefault(owner_id, []).append(row)

        privacy = load_privacy_rules(self.root)
        current_references = references_by_doc.get(target_doc_id, [])
        child_docs = [
            doc for doc in subtree
            if str(doc.get("id") or "") != target_doc_id
        ]
        child_reference_total = sum(
            len(references_by_doc.get(str(doc.get("id") or ""), []))
            for doc in child_docs
        )

        lines = [
            "# 文档引用检测",
            "",
            f"文档：{display_document_path(live_target)}（`{target_doc_id}`）",
            f"本文档总共被引用 {len(current_references)} 次。",
        ]

        if child_docs:
            lines.extend([
                "",
                f"其所有子文档（不含本文档）总共被引用 {child_reference_total} 次，"
                "子文档的被引用情况如下（不包括被隐藏的文档）：",
            ])
            visible_referenced_children = [
                (
                    doc,
                    len(references_by_doc.get(str(doc.get("id") or ""), [])),
                )
                for doc in child_docs
                if document_permission(doc, privacy, live_docs) != "hidden"
                and references_by_doc.get(str(doc.get("id") or ""))
            ]
            visible_referenced_children.sort(
                key=lambda item: (
                    -item[1],
                    display_document_path(item[0]).casefold(),
                    display_document_path(item[0]),
                    str(item[0].get("id") or ""),
                )
            )
            shown_children = (
                visible_referenced_children
                if limit is None
                else visible_referenced_children[:limit]
            )
            if shown_children:
                for child_doc, count in shown_children:
                    lines.append(
                        f"- {display_document_path(child_doc)}"
                        f"（`{child_doc.get('id', '')}`）被引用 {count} 次"
                    )
            else:
                lines.append("无可展示的子文档。")
            remaining_children = len(visible_referenced_children) - len(shown_children)
            if remaining_children > 0:
                lines.append(
                    f"另有 {remaining_children} 篇可见子文档未展示，"
                    '请使用 limit="none" 查看全部引用。'
                )

        source_permission_cache: dict[str, str] = {}

        def source_permission(root_id: str) -> str:
            if root_id not in source_permission_cache:
                source_doc = docs_by_id.get(root_id)
                source_permission_cache[root_id] = (
                    document_permission(source_doc, privacy, live_docs)
                    if source_doc is not None
                    else "hidden"
                )
            return source_permission_cache[root_id]

        visible_groups: dict[str, list[dict[str, Any]]] = {}
        hidden_reference_count = 0
        for row in current_references:
            root_id = str(row.get("root_id") or "")
            if source_permission(root_id) == "hidden":
                hidden_reference_count += 1
            else:
                visible_groups.setdefault(root_id, []).append(row)

        ordered_groups = sorted(
            visible_groups.items(),
            key=lambda item: (
                -len(item[1]),
                display_document_path(docs_by_id[item[0]]).casefold(),
                display_document_path(docs_by_id[item[0]]),
                item[0],
            ),
        )
        shown_groups = ordered_groups if limit is None else ordered_groups[:limit]
        if shown_groups:
            lines.extend(["", "## 引用来源"])
            for source_doc_id, source_rows in shown_groups:
                source_doc = docs_by_id[source_doc_id]
                lines.extend([
                    "",
                    f"### {display_document_path(source_doc)}"
                    f"（`{source_doc_id}`）引用了 {len(source_rows)} 次",
                ])
                rows_by_block: dict[str, list[dict[str, Any]]] = {}
                for row in source_rows:
                    source_block_id = str(row.get("block_id") or "")
                    rows_by_block.setdefault(source_block_id, []).append(row)
                shown_blocks = list(rows_by_block.items())[:MAX_REFERENCE_BLOCKS_PER_DOCUMENT]
                for index, (_source_block_id, block_rows) in enumerate(shown_blocks, start=1):
                    label = f"引用{index}"
                    if len(block_rows) > 1:
                        label += f"（本块包含 {len(block_rows)} 次引用）"
                    lines.extend(["", f"{label}：", reference_block_markdown(block_rows[0])])
                remaining_blocks = len(rows_by_block) - len(shown_blocks)
                if remaining_blocks > 0:
                    lines.extend([
                        "",
                        f"另有 {remaining_blocks} 个引用块未展示，更多引用请查看原文档。",
                    ])

        if hidden_reference_count:
            lines.extend(["", f"隐藏文档中引用了 {hidden_reference_count} 次。"])

        remaining_groups = len(ordered_groups) - len(shown_groups)
        if remaining_groups > 0:
            lines.extend([
                "",
                f"另有 {remaining_groups} 篇可见来源文档未展示，"
                '请使用 limit="none" 查看全部引用。',
            ])
        return "\n".join(lines)

    def _resolve_reference_document(self, args: dict[str, Any], client: Any) -> dict[str, Any]:
        locator = str(args.get("document") or args.get("document_id") or "").strip()
        if not locator:
            raise tool_error(_ERR_MISSING_PARAM, "action=check_references 需要 document 或 document_id。")
        if locator == "/":
            raise tool_error(_ERR_WRONG_TARGET, "action=check_references 只接受文档，不能使用 /。")

        locator_key = locator.strip("/").casefold()
        for notebook in client.list_notebooks():
            notebook_id = str(notebook.get("id") or "")
            notebook_name = str(notebook.get("name") or "")
            if locator == notebook_id or (notebook_name and locator_key == notebook_name.casefold()):
                raise tool_error(
                    _ERR_WRONG_TARGET,
                    "action=check_references 只接受文档，输入不能是笔记本名称或笔记本 ID。",
                )

        try:
            return self.resolve_visible_document(args)
        except ValueError as exc:
            if args.get("document_id") or re.fullmatch(r"\d{14}-[0-9a-z]{7}", locator):
                escaped = locator.replace("'", "''")
                rows = client.query_sql(
                    f"SELECT id, root_id, type FROM blocks WHERE id = '{escaped}' LIMIT 1"
                )
                if rows and str(rows[0].get("type") or "") not in {"d", "NodeDocument"}:
                    raise tool_error(
                        _ERR_WRONG_TARGET,
                        "document_id 指向文档内块，不是文档 ID。请提供其根文档 ID 或文档路径。",
                    ) from exc
            raise

    def siyuan_list(self, args: dict[str, Any]) -> str:
        path = normalize_display_path(str(args.get("path") or "").strip())
        notebook_id = str(args.get("notebook_id") or "").strip()
        notebook_name = str(args.get("notebook_name") or "").strip()
        limit = clamp_int(args.get("limit"), 100, 1, 500)
        offset = max(int(args.get("offset") or 0), 0)

        if (not path or path == "/") and not notebook_id and not notebook_name:
            # List all notebooks
            notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
            docs = load_docs(self.root)
            privacy = load_privacy_rules(self.root)
            lines = ["# 可见笔记本", ""]
            lines.extend([
                "| notebook | notebook_id | 权限 |",
                "|---|---|---|",
            ])
            for notebook in notebooks:
                permission = document_permission(notebook_permission_probe(notebook), privacy, docs)
                lines.append(
                    "| "
                    + " | ".join([
                        str(notebook.get("name", "")),
                        f"`{notebook.get('id', '')}`",
                        permission,
                    ])
                    + " |"
                )
            lines.append("")
            return "\n".join(lines)

        docs = load_docs(self.root)
        privacy = load_privacy_rules(self.root)
        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")

        # Compatibility: old notebook_id/notebook_name args now list the notebook root.
        if not notebook_id and notebook_name:
            notebook_id = self.resolve_notebook_id(notebook_name)
        if notebook_id and not path:
            path = normalize_display_path(self._notebook_name(notebook_id))

        if not path:
            raise tool_error(_ERR_MISSING_PARAM, "path 参数为空。")

        parent_doc = next(
            (doc for doc in docs if display_document_path(doc).casefold() == path.casefold()),
            None,
        )
        notebook = next(
            (nb for nb in notebooks if normalize_display_path(str(nb.get("name", ""))).casefold() == path.casefold()),
            None,
        )
        if parent_doc is None and notebook is None:
            has_descendants = any(
                display_document_path(doc).casefold().startswith(path.casefold() + "/")
                for doc in docs
            )
            if not has_descendants:
                raise FileNotFoundError(f"未找到可见路径：{path}")

        children_by_name: dict[str, dict[str, Any]] = {}
        for doc in docs:
            child_name = direct_child_key(path, display_document_path(doc))
            if not child_name:
                continue
            child_path = normalize_display_path(f"{path}/{child_name}")
            existing = children_by_name.get(child_name)
            exact_doc = display_document_path(doc).casefold() == child_path.casefold()
            if existing is None or exact_doc:
                if exact_doc:
                    children_by_name[child_name] = doc
                else:
                    children_by_name[child_name] = {
                        "id": "",
                        "notebook_id": str(doc.get("notebook_id", "")),
                        "notebook_name": str(doc.get("notebook_name", "")),
                        "hpath": "/" + child_path.strip("/").split("/", 1)[1],
                        "title": child_name,
                        "word_count": 0,
                        "block_count": 0,
                        "updated": "",
                    }

        children = list(children_by_name.values())
        children.sort(key=lambda doc: display_document_path(doc).casefold())
        total = len(children)
        page = children[offset:offset + limit]

        lines = [
            f"# {path}",
            "",
            "| document | document_id | 权限 | 字数 | 块数 | 更新 | 子文档 |",
            "|---|---|---|---:|---:|---|---:|",
        ]
        if not page:
            lines.append("| (无可见子文档) |  |  |  |  |  |  |")
        for doc in page:
            doc_path = display_document_path(doc)
            permission = document_permission(doc, privacy, docs)
            lines.append(
                "| "
                + " | ".join([
                    doc_path,
                    f"`{doc.get('id', '')}`",
                    permission,
                    format_int(doc.get("word_count", 0)),
                    format_int(doc.get("block_count", 0)),
                    format_date(str(doc.get("updated", ""))),
                    format_int(descendant_count(doc, docs)),
                ])
                + " |"
            )
        if offset + limit < total:
            remaining = total - offset - limit
            lines.extend([
                "",
                f"还有 {remaining} 项未显示。",
                f"继续：siyuan_list(path=\"{path}\", offset={offset + limit}, limit={limit})",
            ])
        return "\n".join(lines)

    def _notebook_name(self, notebook_id: str) -> str:
        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
        for nb in notebooks:
            if str(nb.get("id", "")) == notebook_id:
                return str(nb.get("name", notebook_id))
        return notebook_id

    def siyuan_find(self, args: dict[str, Any]) -> str:
        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            raise tool_error(_ERR_MISSING_PARAM, "keyword 参数是必填的")

        mode = str(args.get("mode") or "query").strip().casefold()
        if mode not in ("keyword", "query", "regex", "sql"):
            raise tool_error(_ERR_INVALID_ENUM, "mode 必须是 query、regex 或 sql 之一")
        if mode == "keyword":
            # Backward compatibility for old clients; keyword is no longer a
            # distinct public mode and uses query semantics throughout.
            mode = "query"

        scope = str(args.get("scope") or "headings").strip().casefold()
        if scope not in ("headings", "full"):
            raise tool_error(_ERR_INVALID_ENUM, "scope 必须是 headings 或 full 之一")

        limit = max(int(args.get("limit") or 20), 1)
        max_snippets_per_doc = max(int(args.get("max_snippets_per_doc") or DEFAULT_SNIPPETS_PER_DOC), 1)

        notebooks_raw = args.get("notebooks")
        notebooks: list[str] | None = None
        if notebooks_raw and notebooks_raw != "ALL":
            if isinstance(notebooks_raw, list):
                notebooks = [str(n) for n in notebooks_raw if n]
            elif isinstance(notebooks_raw, str) and notebooks_raw.strip().upper() != "ALL":
                notebooks = [notebooks_raw.strip()]
            if not notebooks:
                notebooks = None

        privacy = load_privacy_rules(self.root)
        indexed_docs = load_docs(self.root)
        notebook_names = self.load_notebook_names()

        if mode == "sql":
            client = self._require_active_client()
            notebook_names.update(list_live_notebook_names(client))
            try:
                with ensure_notebooks_open(client, notebooks):
                    rows = client.query_sql(keyword)
            except SiYuanApiError as exc:
                if "administrator" in str(exc).casefold() or "privilege" in str(exc).casefold():
                    raise tool_error(_ERR_SQL_ADMIN, "SQL 搜索需要思源管理员权限，请改用 keyword、query 或 regex 模式。") from exc
                raise
            enriched = self._enrich_sql_results(rows, indexed_docs, notebook_names, privacy, notebooks)
        else:
            client = self._require_active_client()
            notebook_names.update(list_live_notebook_names(client))
            method_map = {"query": 1, "regex": 3}
            api_method = method_map[mode]
            with ensure_notebooks_open(client, notebooks):
                data = search_content(
                    client,
                    keyword,
                    method=api_method,
                    scope=scope,
                    notebooks=notebooks,
                    limit=limit,
                )
            blocks: list[dict[str, Any]] = data.get("blocks", [])
            keywords = search_terms(keyword, mode)
            enriched = self._enrich_search_blocks(blocks, indexed_docs, notebook_names, privacy, keywords, notebooks)

        if not enriched:
            return f"# 搜索：\"{keyword}\"（{scope}，{mode}）\n\n未找到匹配的可见文档。"

        enriched = enriched[:limit]
        grouped = self._group_by_notebook(enriched)

        scope_label = "标题" if scope == "headings" else "全文"
        lines = [f"# 搜索：\"{keyword}\"（{scope_label}，{mode}，{len(enriched)} 条结果，{len(grouped)} 个笔记本）", ""]

        remaining = limit
        for nb_name in sorted(grouped, key=str.casefold):
            items = grouped[nb_name]
            lines.append(f"## {nb_name}（{len(items)} 条命中）")
            for item in items[:remaining]:
                wc = item.get("word_count", 0)
                bc = item.get("block_count", 0)
                date = format_date(str(item.get("updated", "")))
                hpath = str(item.get("hpath") or "/")
                doc_id = str(item.get("id") or "")
                source = str(item.get("source") or "")
                source_text = f" [{source}]" if source else ""
                lines.append(f"- `{doc_id}` {hpath} {wc:,}字 {bc}块 {date}{source_text}".rstrip())
                snippets = item.get("snippets")
                if isinstance(snippets, list):
                    shown_snippets = snippets[:max_snippets_per_doc]
                    match_count = int(item.get("match_count") or len(snippets))
                    lines.append(f"  命中块：共 {match_count} 个，展示前 {len(shown_snippets)} 个。")
                    for snippet in shown_snippets:
                        if isinstance(snippet, dict):
                            block_id = str(snippet.get("block_id") or "")
                            text = str(snippet.get("text") or "")
                            if block_id and text:
                                lines.append(f"  > `{block_id}` {text}")
                            elif text:
                                lines.append(f"  > {text}")
                else:
                    snippet = item.get("snippet", "")
                    if snippet:
                        lines.append(f"  > {snippet}")
            lines.append("")
            remaining -= len(items)
            if remaining <= 0:
                break

        return "\n".join(lines)

    def _enrich_search_blocks(
        self,
        blocks: list[dict[str, Any]],
        indexed_docs: list[dict[str, Any]],
        notebook_names: dict[str, str],
        privacy: Any,
        keywords: list[str],
        notebook_filter: list[str] | None,
    ) -> list[dict[str, Any]]:
        doc_index = {str(doc.get("id", "")): doc for doc in indexed_docs}
        compiled_ignore = compile_rules(privacy.ignore, indexed_docs)
        compiled_allow = compile_rules(privacy.allow, indexed_docs)

        results_by_doc: dict[str, dict[str, Any]] = {}
        seen_blocks: set[str] = set()

        for block in blocks:
            doc_id = block_document_id(block)
            block_id = str(block.get("id") or "")
            if not doc_id or (block_id and block_id in seen_blocks):
                continue

            doc = live_doc_from_block(block, doc_index, notebook_names)
            nb_id = str(doc.get("notebook_id", ""))
            if notebook_filter and nb_id not in notebook_filter:
                continue
            if not is_live_doc_visible(doc, compiled_ignore, compiled_allow):
                continue
            # Hard-filter Privacy Rules document
            if is_privacy_rules_document(
                str(doc.get("hpath", "")),
                root=self.root,
                document_id=doc_id,
                notebook_id=nb_id,
            ):
                continue

            if block_id:
                seen_blocks.add(block_id)

            content = str(block.get("markdown") or block.get("content") or "")
            snippet = extract_snippet(content, keywords)

            result = results_by_doc.get(doc_id)
            if result is None:
                result = {
                    "id": doc_id,
                    "notebook_id": nb_id,
                    "notebook_name": str(doc.get("notebook_name", "")),
                    "hpath": str(doc.get("hpath", "")),
                    "word_count": doc.get("word_count", 0),
                    "block_count": doc.get("block_count", 0),
                    "updated": str(doc.get("updated", "")),
                    "snippet": snippet,
                    "snippets": [],
                    "match_count": 0,
                    "source": "实时搜索",
                }
                results_by_doc[doc_id] = result
            result["match_count"] += 1
            if snippet:
                result["snippets"].append({"block_id": block_id, "text": snippet})

        results = list(results_by_doc.values())
        results.sort(key=lambda r: (r["notebook_name"].casefold(), r["hpath"].casefold()))
        return results

    def _enrich_sql_results(
        self,
        rows: list[dict[str, Any]],
        indexed_docs: list[dict[str, Any]],
        notebook_names: dict[str, str],
        privacy: Any,
        notebook_filter: list[str] | None,
    ) -> list[dict[str, Any]]:
        doc_index = {str(doc.get("id", "")): doc for doc in indexed_docs}
        compiled_ignore = compile_rules(privacy.ignore, indexed_docs)
        compiled_allow = compile_rules(privacy.allow, indexed_docs)

        seen: set[str] = set()
        results: list[dict[str, Any]] = []

        for row in rows:
            doc_id = block_document_id(row)
            if not doc_id or doc_id in seen:
                continue

            doc = live_doc_from_block(row, doc_index, notebook_names)
            nb_id = str(doc.get("notebook_id", ""))
            if notebook_filter and nb_id not in notebook_filter:
                continue
            if not is_live_doc_visible(doc, compiled_ignore, compiled_allow):
                continue
            # Hard-filter Privacy Rules document
            if is_privacy_rules_document(
                str(doc.get("hpath", "")),
                root=self.root,
                document_id=doc_id,
                notebook_id=nb_id,
            ):
                continue

            seen.add(doc_id)
            results.append({
                "id": doc_id,
                "notebook_id": nb_id,
                "notebook_name": str(doc.get("notebook_name", "")),
                "hpath": str(doc.get("hpath", "")),
                "word_count": doc.get("word_count", 0),
                "block_count": doc.get("block_count", 0),
                "updated": str(doc.get("updated", "")),
                "snippet": "",
                "source": "sql",
            })

        results.sort(key=lambda r: (r["notebook_name"].casefold(), r["hpath"].casefold()))
        return results

    @staticmethod
    def _group_by_notebook(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in results:
            nb = str(item.get("notebook_name") or "Unknown")
            groups.setdefault(nb, []).append(item)
        return groups

    def load_notebook_names(self) -> dict[str, str]:
        path = self.root / KNOWLEDGE_BASE_DIR / "notebooks.json"
        if not path.exists():
            return {}
        try:
            notebooks = read_json(path)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(notebooks, list):
            return {}
        return {
            str(notebook.get("id", "")): str(notebook.get("name", ""))
            for notebook in notebooks
            if isinstance(notebook, dict)
        }

    def siyuan_read(self, args: dict[str, Any]) -> str:
        doc = self.resolve_visible_document(args)
        client = self._require_active_client()
        include_block_ids = bool(args.get("include_block_ids"))
        return self._read_document_block_window(doc, client, include_block_ids, args)

    def _read_document_block_window(
        self, doc: dict[str, Any], client: Any, include_block_ids: bool, args: dict[str, Any]
    ) -> str:
        """New block window reading path — uses getChildBlocks for display order."""
        doc_id = str(doc.get("id"))
        notebook_id = str(doc.get("notebook_id", ""))

        with ensure_notebooks_open(client, [notebook_id]):
            display_blocks = build_display_blocks(client, doc_id, include_block_ids=include_block_ids)

        # Fallback: if block build returns empty (e.g., very unusual document), use export
        if not display_blocks:
            with ensure_notebooks_open(client, [notebook_id]):
                markdown = client.export_markdown(doc_id)
            attachment_count = extract_attachments(markdown, client, doc_id, self.root)
            markdown = rewrite_local_asset_links(markdown, doc_id, self.root)
            doc_path = display_document_path(doc)
            date = format_date(str(doc.get("updated", "")))
            header_lines = [
                f"# 文档：{doc_path}",
                f"文档 ID：`{doc_id}`",
                f"更新：{date}",
                "阅读模式：普通阅读（降级到导出 Markdown）",
            ]
            if attachment_count:
                header_lines.append(f"附件：{attachment_count} 个已提取到 {attachment_root_dir(self.root, doc_id).resolve()}")
            return "\n".join(["\n".join(header_lines), "", "---", "", markdown])

        # Compute stats
        total_blocks = len(display_blocks)
        heading_count = sum(1 for b in display_blocks if b.is_heading)

        # Extract attachments from the markdown (use export for attachment discovery)
        with ensure_notebooks_open(client, [notebook_id]):
            full_md = client.export_markdown(doc_id)
        attachment_count = extract_attachments(full_md, client, doc_id, self.root)

        # Clamp block window params
        block_start = max(int(args.get("block_start") or 1), 1)
        block_limit = clamp_int(args.get("block_limit"), DEFAULT_BLOCK_LIMIT, MIN_BLOCK_LIMIT, MAX_BLOCK_LIMIT)
        token_budget = clamp_int(args.get("token_budget"), DEFAULT_TOKEN_BUDGET, MIN_TOKEN_BUDGET, MAX_TOKEN_BUDGET)

        # Select window
        start_idx = max(block_start - 1, 0)
        end_idx = min(start_idx + block_limit, total_blocks)

        # Apply token budget — include at least one block
        window_blocks: list[DisplayBlock] = []
        token_sum = 0
        for db in display_blocks[start_idx:end_idx]:
            if window_blocks and token_sum + db.estimated_tokens > token_budget:
                break
            window_blocks.append(db)
            token_sum += db.estimated_tokens

        window_tokens = token_sum
        first_idx = window_blocks[0].index if window_blocks else start_idx + 1
        last_idx = window_blocks[-1].index if window_blocks else start_idx

        # Build header
        doc_path = display_document_path(doc)
        date = format_date(str(doc.get("updated", "")))
        mode_label = "引用阅读（显示块序号、ID 和类型）" if include_block_ids else "普通阅读"
        header_lines = [
            f"# 文档：{doc_path}",
            f"文档 ID：`{doc_id}`",
            f"更新：{date}",
            f"阅读模式：{mode_label}",
            f"展示块：{first_idx}-{last_idx} / {total_blocks}",
            f"估算令牌数：{window_tokens:,} / {token_budget:,}",
        ]
        if start_idx + block_limit < total_blocks:
            next_start = last_idx + 1
            header_lines.append(f"下一窗口：block_start={next_start}, block_limit={block_limit}")
        if attachment_count:
            header_lines.append(f"附件：{attachment_count} 个已提取到 {attachment_root_dir(self.root, doc_id).resolve()}")
        header = "\n".join(header_lines)

        # Build outline (always full document outline with block positions)
        outline = build_block_outline(display_blocks)

        # Build window preview (only when headings < 5 AND total blocks > 100)
        window_preview = build_window_preview(display_blocks)

        # Build block text for current window
        body_lines: list[str] = []
        for db in window_blocks:
            if db.markdown.strip():
                body_lines.append(db.markdown)
        body = "\n\n".join(body_lines)
        body = rewrite_local_asset_links(body, doc_id, self.root)

        parts = [header, "", outline]
        if window_preview:
            parts.extend(["", window_preview])
        parts.extend(["", "---", "", body])

        if last_idx < total_blocks:
            parts.extend([
                "",
                "---",
                f"> 继续阅读：`block_start={last_idx + 1}, block_limit={block_limit}`",
            ])

        return "\n".join(parts)

    def resolve_visible_document(self, args: dict[str, Any]) -> dict[str, Any]:
        locator = str(args.get("document") or args.get("document_id") or args.get("locator") or "").strip()
        if not locator:
            raise tool_error(_ERR_MISSING_PARAM, "document/document_id 参数是必填的")
        locator_is_path = locator.startswith("/")
        docs = filter_documents(load_docs(self.root), load_privacy_rules(self.root))
        if locator_is_path:
            exact_display_path = [
                doc
                for doc in docs
                if display_document_path(doc).strip("/").casefold() == locator.strip("/").casefold()
            ]
            if exact_display_path:
                if len(exact_display_path) > 1:
                    choices = "\n".join(f"- `{doc.get('id')}` {display_document_path(doc)}" for doc in exact_display_path)
                    raise tool_error(_ERR_AMBIGUOUS, f"文档路径存在歧义，请补充 document_id：\n{choices}")
                doc = exact_display_path[0]
                if is_privacy_rules_document(
                    str(doc.get("hpath", "")),
                    root=self.root,
                    document_id=str(doc.get("id") or ""),
                    notebook_id=str(doc.get("notebook_id") or ""),
                ):
                    raise tool_error(_ERR_PRIVACY_RULES,
                        "Privacy Rules 文档不可通过 AI 访问。隐私规则由人类在思源中维护。"
                    )
                self._ensure_document_path_current(doc, locator)
                return doc
        status, matches = resolve_document(docs, locator)
        if status == "ambiguous":
            choices = "\n".join(f"- `{doc.get('id')}` {doc.get('hpath')}" for doc in matches)
            raise tool_error(_ERR_AMBIGUOUS, f"文档定位符存在歧义：\n{choices}")
        if status in ("missing", "no_index"):
            privacy = load_privacy_rules(self.root)
            if privacy.allow:
                client = self._require_active_client()
                with ensure_notebooks_open(client):
                    live_docs = filter_documents(load_live_docs(client), privacy)
                status, matches = resolve_document(live_docs, locator)
        if status != "ok":
            raise tool_error(_ERR_DOC_NOT_FOUND, "未找到匹配的可见文档。文档可能已被隐藏、尚未索引，或定位符有误。")
        doc = matches[0]
        if is_privacy_rules_document(
            str(doc.get("hpath", "")),
            root=self.root,
            document_id=str(doc.get("id") or ""),
            notebook_id=str(doc.get("notebook_id") or ""),
        ):
            raise tool_error(_ERR_PRIVACY_RULES,
                "Privacy Rules 文档不可通过 AI 访问。隐私规则由人类在思源中维护。"
            )
        if locator_is_path:
            self._ensure_document_path_current(doc, locator)
        return doc

    def _ensure_document_path_current(self, doc: dict[str, Any], requested_path: str) -> None:
        doc_id = str(doc.get("id") or "").strip()
        if not doc_id:
            raise tool_error(_ERR_DOC_NOT_FOUND, "未找到匹配的可见文档。文档可能已被隐藏、尚未索引，或定位符有误。")
        client = self._require_active_client()
        try:
            live_hpath = normalize_display_path(client.get_hpath_by_id(doc_id))
        except Exception as exc:
            raise tool_error(_ERR_STALE_DOCUMENT_PATH,
                "无法确认文档当前路径。请先调用 `siyuan_operate(action=\"refresh\")` 刷新索引，"
                "然后用新路径重试；或改用 document_id。"
            ) from exc

        live_doc = dict(doc)
        live_doc["hpath"] = live_hpath
        live_display_path = normalize_display_path(display_document_path(live_doc))
        requested = normalize_display_path(requested_path)
        valid_paths = {
            live_hpath.casefold(),
            live_display_path.casefold(),
        }
        if requested.casefold() in valid_paths:
            return

        raise tool_error(_ERR_STALE_DOCUMENT_PATH,
            "文档路径已过期，已停止操作。\n"
            f"请求路径：{requested}\n"
            f"当前真实路径：{live_display_path}\n"
            "请先调用 `siyuan_operate(action=\"refresh\")` 刷新索引，然后用当前真实路径重试；"
            "或改用 document_id。"
        )

    def export_document_markdown(self, document_id: str) -> str:
        client = self._require_active_client()
        return client.export_markdown(document_id)

    def siyuan_create(self, args: dict[str, Any]) -> str:
        confirmed = bool(args.get("confirmed"))
        if not confirmed:
            raise tool_error(_ERR_NOT_CONFIRMED, "需要 confirmed=true。写入思源必须经过用户明确确认。")

        title = str(args.get("title") or "").strip()
        if not title:
            raise tool_error(_ERR_MISSING_PARAM, "title 参数是必填的")

        markdown = resolve_markdown_input(args, require=True).strip()
        if not markdown:
            raise tool_error(_ERR_MISSING_PARAM, "markdown 或 markdown_file 内容不能为空。")

        if_exists = str(args.get("if_exists") or "reject").strip().casefold()
        if if_exists not in {"reject", "overwrite", "create_new"}:
            raise tool_error(_ERR_INVALID_ENUM, "if_exists 只支持 reject、overwrite、create_new。默认 reject。")

        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
        docs = filter_documents(load_docs(self.root), load_privacy_rules(self.root))
        target = resolve_create_target(args, notebooks, docs, title)
        privacy = load_privacy_rules(self.root)
        client = self._require_active_client()
        all_docs = load_live_docs(client)
        target.existing_docs = _existing_docs_at_path(
            filter_documents(all_docs, privacy),
            target.notebook_id,
            target.internal_path,
        )
        target_doc_for_permission = {
            "id": "",
            "notebook_id": target.notebook_id,
            "notebook_name": target.notebook_name,
            "hpath": target.internal_path,
        }
        if document_permission(target_doc_for_permission, privacy, all_docs) != "read_write":
            raise tool_error(_ERR_NOT_READ_WRITE, "目标路径权限不是 read_write，不允许创建或覆盖文档。")

        # Prevent creating Privacy Rules document
        if is_privacy_rules_document(
            target.internal_path.strip("/"),
            root=self.root,
            notebook_id=target.notebook_id,
        ):
            raise tool_error(_ERR_PRIVACY_RULES,
                "Privacy Rules 文档不可通过 AI 创建。隐私规则由人类在思源中维护。"
            )
        for existing in target.existing_docs:
            if document_permission(existing, privacy, all_docs) != "read_write":
                raise tool_error(_ERR_NOT_READ_WRITE, f"目标文档权限不是 read_write，不允许写入：{display_document_path(existing)}")

        if target.existing_docs and if_exists == "reject":
            choices = "\n".join(
                f"- `{doc.get('id', '')}` {display_document_path(doc)}"
                for doc in target.existing_docs
            )
            raise tool_error(_ERR_ALREADY_EXISTS,
                "目标文档已存在，默认拒绝写入以避免误覆盖。\n"
                "可选处理：if_exists=overwrite 清空当前文档所有块后重写，并保留文档 ID；"
                "if_exists=create_new 新增一个同名文档。\n"
                + choices
            )
        if len(target.existing_docs) > 1 and if_exists == "overwrite":
            choices = "\n".join(
                f"- `{doc.get('id', '')}` {display_document_path(doc)}"
                for doc in target.existing_docs
            )
            raise tool_error(_ERR_MULTI_DOC_OVERWRITE,
                "目标路径下已有多个同名文档，无法判断覆盖时应保留哪个文档 ID。"
                "请先用 siyuan_edit 定位具体文档，或使用 if_exists=create_new。\n"
                + choices
            )

        reference_notice = ""
        existing_doc: dict[str, Any] | None = target.existing_docs[0] if target.existing_docs else None
        if existing_doc and if_exists == "overwrite":
            existing_doc_id = str(existing_doc.get("id") or "")
            with ensure_notebooks_open(client, [target.notebook_id]):
                deleting_ids = {
                    str(block.get("id") or "")
                    for block in client.list_document_blocks(existing_doc_id)
                    if str(block.get("id") or "")
                }
            reference_notice = self._protect_referenced_blocks(client, deleting_ids, args)

        # Create snapshot before writing
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        memo = f"siyuan-bridge:auto-snapshot tool=siyuan_create target={target.display_path} created={ts}"
        try:
            client.create_snapshot(memo)
            snapshot_status = "created"
        except SiYuanApiError as exc:
            msg = str(exc)
            if "数据仓库密钥" in msg or "data repo key" in msg.casefold() or "key" in msg.casefold():
                raise tool_error(_ERR_SNAPSHOT_KEY,
                    "快照创建失败：数据仓库密钥未初始化。"
                    "请打开思源 → 设置 → 关于 → 数据仓库密钥，初始化密钥后重试。"
                ) from exc
            raise tool_error(_ERR_SNAPSHOT_FAILED, f"快照创建失败，拒绝写入。错误：{msg}") from exc

        # Normalize markdown to avoid duplicate H1
        markdown = normalize_new_document_markdown(title, markdown)
        if not markdown.strip():
            raise tool_error(_ERR_MISSING_PARAM, "markdown 参数是必填的")

        action_status = "created"
        overwritten_blocks: list[DisplayBlock] = []

        with ensure_notebooks_open(client, [target.notebook_id]):
            if existing_doc and if_exists == "overwrite":
                doc_id = str(existing_doc.get("id", ""))
                overwritten_blocks = build_display_blocks(client, doc_id, include_block_ids=True)
                for block in reversed(overwritten_blocks):
                    client.delete_block(block.id)
                client.append_block(doc_id, markdown)
                result = {"id": doc_id}
                action_status = "overwritten"
            else:
                result = client.create_doc_with_md(target.notebook_id, target.internal_path, markdown)
                action_status = "created_new" if existing_doc and if_exists == "create_new" else "created"

        doc_id = str(result.get("id") or result.get("docID") or result.get("doc_id") or "")
        if not doc_id:
            # Try to resolve by path
            try:
                live_docs = load_live_docs(client)
                for doc in live_docs:
                    if (
                        str(doc.get("hpath", "")).strip("/") == target.internal_path.strip("/")
                        and str(doc.get("notebook_id", "")) == target.notebook_id
                        and str(doc.get("id", "")) not in {str(item.get("id", "")) for item in target.existing_docs}
                    ):
                        doc_id = str(doc.get("id", ""))
                        break
            except Exception:
                pass

        # Notify
        try:
            client.push_msg(f"思源桥：已写入「{target.display_path}」")
        except Exception:
            pass

        sync_status: PostWriteSyncStatus | None = None
        if doc_id:
            sync_status = self._wait_for_hpath(client, doc_id, target.internal_path)

        # Auto-refresh index
        refresh_ok = False
        try:
            self._refresh_index_with_system_context(client)
            refresh_ok = True
        except Exception:
            pass

        parts = [
            "# 文档写入成功",
            "",
            f"**动作：**{action_status}",
            f"**标题：**{title}",
            f"**路径：**{target.display_path}",
            f"**内部路径：**{target.internal_path}",
            f"**笔记本：**{target.notebook_name}（`{target.notebook_id}`）",
        ]
        if doc_id:
            parts.append(f"**文档 ID：**`{doc_id}`")
        if overwritten_blocks:
            parts.append(f"**覆盖：**已清空并重写 {len(overwritten_blocks)} 个原块，保留当前文档 ID。")
        if reference_notice:
            parts.append(f"**引用保护：**{reference_notice}")
        parts.append(f"**端点：**{client.base_url}")
        parts.append(f"**快照：**{snapshot_status}")
        if sync_status is not None:
            parts.append(f"**路径同步：**{sync_status.detail}")
        if refresh_ok:
            parts.append(f"**索引：**已自动刷新")
        else:
            parts.append(f"**索引：**自动刷新失败，请手动运行 `siyuan_operate(action=\"refresh\")`")
        parts.extend([
            "",
            "如需回滚，可通过思源快照手动恢复。",
        ])
        return "\n".join(parts)

    @staticmethod
    def _create_snapshot_or_raise(client: Any, tool: str, target: str) -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        memo = f"siyuan-bridge:auto-snapshot tool={tool} target={target} created={ts}"
        try:
            client.create_snapshot(memo)
            return "created"
        except SiYuanApiError as exc:
            msg = str(exc)
            if "数据仓库密钥" in msg or "data repo key" in msg.casefold() or "key" in msg.casefold():
                raise tool_error(_ERR_SNAPSHOT_KEY,
                    "快照创建失败：数据仓库密钥未初始化。"
                    "请打开思源 -> 设置 -> 关于 -> 数据仓库密钥，初始化密钥后重试。"
                ) from exc
            raise tool_error(_ERR_SNAPSHOT_FAILED, f"快照创建失败，拒绝写入。错误：{msg}") from exc

    def _protect_referenced_blocks(
        self,
        client: Any,
        deleting_ids: set[str],
        args: dict[str, Any],
    ) -> str:
        policy = str(args.get("reference_policy") or "reject").strip().casefold()
        if policy not in {"reject", "break"}:
            raise tool_error(_ERR_INVALID_ENUM, "reference_policy 只支持 reject 或 break，默认 reject。")
        deleting_ids = {block_id for block_id in deleting_ids if block_id}
        if not deleting_ids:
            return ""

        with ensure_notebooks_open(client):
            live_docs = load_live_docs(client)
            references = client.list_block_references(sorted(deleting_ids))

        external_refs = [
            row for row in references
            if str(row.get("block_id") or "") not in deleting_ids
            and str(row.get("root_id") or "") not in deleting_ids
        ]
        if not external_refs:
            return ""

        referenced_target_ids = {
            str(row.get("def_block_id") or "")
            for row in external_refs
            if str(row.get("def_block_id") or "")
        }
        if policy == "break":
            return (
                f"用户已明确允许破坏引用；"
                f"本次删除 {len(referenced_target_ids)} 个被引用块 ID，影响 {len(external_refs)} 处引用关系。"
            )

        privacy = load_privacy_rules(self.root)
        docs_by_id = {str(doc.get("id") or ""): doc for doc in live_docs}
        permission_cache: dict[str, str] = {}

        def source_permission(root_id: str) -> str:
            if root_id not in permission_cache:
                source_doc = docs_by_id.get(root_id)
                permission_cache[root_id] = (
                    document_permission(source_doc, privacy, live_docs)
                    if source_doc is not None
                    else "hidden"
                )
            return permission_cache[root_id]

        lines = [
            "操作已拒绝：本次操作会破坏现有引用关系。",
            "",
            f"即将删除 {len(deleting_ids)} 个块 ID，其中 "
            f"{len(referenced_target_ids)} 个仍被 {len(external_refs)} 处引用。",
        ]
        for target_id in sorted(referenced_target_ids):
            target_refs = [
                row for row in external_refs
                if str(row.get("def_block_id") or "") == target_id
            ]
            source_blocks = {
                str(row.get("block_id") or "")
                for row in target_refs
                if str(row.get("block_id") or "")
            }
            source_docs = {
                str(row.get("root_id") or "")
                for row in target_refs
                if str(row.get("root_id") or "")
            }
            visible_refs = [
                row for row in target_refs
                if source_permission(str(row.get("root_id") or "")) != "hidden"
            ]
            hidden_refs = [
                row for row in target_refs
                if source_permission(str(row.get("root_id") or "")) == "hidden"
            ]
            lines.extend([
                "",
                f"## 被引用块 `{target_id}`",
                f"- 引用次数：{len(target_refs)}",
                f"- 引用块：{len(source_blocks)}",
                f"- 引用文档：{len(source_docs)}",
            ])

            if visible_refs:
                lines.extend(["", "可见引用："])
                seen_visible: set[tuple[str, str]] = set()
                visible_count = 0
                for row in visible_refs:
                    root_id = str(row.get("root_id") or "")
                    source_block_id = str(row.get("block_id") or "")
                    key = (root_id, source_block_id)
                    if key in seen_visible:
                        continue
                    seen_visible.add(key)
                    if visible_count >= MAX_REFERENCE_DETAILS_PER_BLOCK:
                        continue
                    source_doc = docs_by_id[root_id]
                    lines.extend([
                        f"- 文档：{display_document_path(source_doc)}",
                        f"  引用块：`{source_block_id}`",
                        f"  内容：{reference_excerpt(row)}",
                    ])
                    visible_count += 1
                remaining = len(seen_visible) - visible_count
                if remaining > 0:
                    lines.append(f"- 其余 {remaining} 个可见引用块未展开。")

            if hidden_refs:
                hidden_docs = {
                    str(row.get("root_id") or "")
                    for row in hidden_refs
                    if str(row.get("root_id") or "")
                }
                lines.extend([
                    "",
                    f"受保护引用：另有 {len(hidden_refs)} 次引用来自 "
                    f"{len(hidden_docs)} 篇受保护文档，具体信息已隐藏。",
                ])

        lines.extend([
            "",
            "## 如何处理这些被引用块",
            "",
            "- 如果修改后仍是同一个事实、观点、任务或条目，只是纠错、扩写或改写，"
            "应保留对应块 ID：改为单块更新，并把新增内容插入其前后。",
            "- 如果原内容已被撤销、合并或整体替代，继续保留 ID 会让现有引用指向"
            "含义不同或具有误导性的内容，才考虑破坏引用。",
            "- 多块操作应逐个判断上面列出的被引用 ID。只要其中仍有应保留的块，"
            "就应先重新规划操作，而不是直接放行整段删除。",
            "",
            "默认拒绝本次操作。只有用户明确允许破坏上述引用关系后，"
            "才能使用相同参数并额外传入 `reference_policy=\"break\"` 重试。",
        ])
        raise tool_error(_ERR_REFERENCED_BLOCKS, "\n".join(lines))

    @staticmethod
    def _update_block_preserving_attrs(client: Any, block_id: str, markdown: str) -> None:
        ial_rows = client.query_sql(f"SELECT ial FROM blocks WHERE id = '{block_id}'")
        custom_attrs: dict[str, str] = {}
        if ial_rows:
            custom_attrs = _parse_ial_attrs(str(ial_rows[0].get("ial", "")))
        client.update_block(block_id, markdown)
        if custom_attrs:
            client.set_block_attrs(block_id, custom_attrs)

    @staticmethod
    def _edit_range_from_args(args: dict[str, Any], blocks: list[DisplayBlock]) -> list[DisplayBlock]:
        if args.get("start_index") is None or not str(args.get("start_id") or "").strip():
            raise tool_error(_ERR_MISSING_EDIT_RANGE, "需要 start_index 和 start_id。请先用 siyuan_read(include_block_ids=true) 进行引用阅读。")
        try:
            start_index = int(args["start_index"])
        except (TypeError, ValueError) as exc:
            raise tool_error(_ERR_INVALID_TYPE, "start_index 必须是整数。") from exc
        start_id = str(args.get("start_id") or "").strip()
        start_pos = next((i for i, block in enumerate(blocks) if block.index == start_index), None)
        if start_pos is None:
            raise tool_error(_ERR_BLOCK_NOT_FOUND,
                f"目标块校验失败：当前文档没有 start_index={start_index}。"
                "文档可能在上次读取后发生变化。请重新调用 siyuan_read(include_block_ids=true)，"
                "用新的块序号和块 ID 再编辑。"
            )
        if blocks[start_pos].id != start_id:
            raise tool_error(_ERR_STALE_BLOCK_ID,
                f"目标块校验失败：start_index={start_index} 对应的当前块 ID 是 `{blocks[start_pos].id}`，"
                f"但请求中的 start_id 是 `{start_id}`。请重新调用 siyuan_read(include_block_ids=true)，"
                "不要沿用旧块 ID。"
            )

        if args.get("end_index") is None and not str(args.get("end_id") or "").strip():
            return [blocks[start_pos]]
        if args.get("end_index") is None or not str(args.get("end_id") or "").strip():
            raise tool_error(_ERR_MISSING_EDIT_RANGE, "范围操作需要同时提供 end_index 和 end_id。")
        try:
            end_index = int(args["end_index"])
        except (TypeError, ValueError) as exc:
            raise tool_error(_ERR_INVALID_TYPE, "end_index 必须是整数。") from exc
        end_id = str(args.get("end_id") or "").strip()
        end_pos = next((i for i, block in enumerate(blocks) if block.index == end_index), None)
        if end_pos is None:
            raise tool_error(_ERR_BLOCK_NOT_FOUND,
                f"目标块校验失败：当前文档没有 end_index={end_index}。"
                "文档可能在上次读取后发生变化。请重新调用 siyuan_read(include_block_ids=true)，"
                "用新的范围端点再编辑。"
            )
        if blocks[end_pos].id != end_id:
            raise tool_error(_ERR_STALE_BLOCK_ID,
                f"目标块校验失败：end_index={end_index} 对应的当前块 ID 是 `{blocks[end_pos].id}`，"
                f"但请求中的 end_id 是 `{end_id}`。请重新调用 siyuan_read(include_block_ids=true)，"
                "不要沿用旧块 ID。"
            )
        if end_pos < start_pos:
            raise tool_error(_ERR_OPERATION_ORDER, "范围操作要求 start_index <= end_index。")
        return blocks[start_pos:end_pos + 1]

    def siyuan_edit(self, args: dict[str, Any]) -> str:
        confirmed = bool(args.get("confirmed"))
        if not confirmed:
            raise tool_error(_ERR_NOT_CONFIRMED, "需要 confirmed=true。编辑思源文档必须经过用户明确确认。")

        action = str(args.get("action") or "").strip()
        allowed_actions = {
            "single_block_replace",
            "multi_block_replace",
            "insert_after",
            "insert_before",
            "append",
            "delete",
            "table_edit",
            "insert_assets",
        }
        if action not in allowed_actions:
            raise tool_error(_ERR_INVALID_ENUM,
                "action 只支持 single_block_replace、multi_block_replace、"
                "insert_after、insert_before、append、delete、table_edit、insert_assets。"
            )

        doc = self.resolve_visible_document(args)
        doc_id = str(doc.get("id", ""))
        doc_title = display_document_path(doc)
        notebook_id = str(doc.get("notebook_id", ""))
        all_docs = load_docs(self.root)
        permission = document_permission(doc, load_privacy_rules(self.root), all_docs)
        if permission != "read_write":
            raise tool_error(_ERR_NOT_READ_WRITE, f"当前文档权限为 {permission}，不允许编辑。")

        client = self._require_active_client()

        with ensure_notebooks_open(client, [notebook_id]):
            display_blocks = build_display_blocks(client, doc_id, include_block_ids=True)

        target_blocks: list[DisplayBlock] = []
        if action != "append":
            target_blocks = self._edit_range_from_args(args, display_blocks)
        markdown = ""
        if action in {"single_block_replace", "multi_block_replace", "insert_after", "insert_before", "append"}:
            markdown = resolve_markdown_input(args, require=True)
            if not markdown.strip():
                raise tool_error(_ERR_MISSING_PARAM, f"action={action} 需要 markdown 或 markdown_file。")
        if action == "table_edit" and not isinstance(args.get("table_edit"), dict):
            raise tool_error(_ERR_MISSING_PARAM, "action=table_edit 需要 table_edit 对象。")

        asset_items: list[AssetInsertionItem] = []
        if action == "insert_assets":
            if args.get("end_index") is not None or str(args.get("end_id") or "").strip():
                raise tool_error(
                    _ERR_WRONG_SHAPE,
                    "action=insert_assets 一次只支持 start_index/start_id 这一处锚点，"
                    "不要传 end_index/end_id。多个位置请分次调用并重新引用阅读。",
                )
            if args.get("upload_large_files") is not None and not isinstance(args.get("upload_large_files"), bool):
                raise tool_error(_ERR_INVALID_TYPE, "upload_large_files 必须是 boolean。")
            asset_items = preflight_asset_items(args.get("assets"))
            large_files = [
                {
                    "local_path": item.local_path,
                    "size_bytes": item.size_bytes,
                }
                for item in asset_items
                if item.size_bytes is not None
                and item.size_bytes > ASSET_LARGE_FILE_THRESHOLD_BYTES
            ]
            if large_files and not bool(args.get("upload_large_files")):
                return json.dumps(
                    {
                        "ok": False,
                        "action": "insert_assets",
                        "requires_confirmation": True,
                        "threshold_bytes": ASSET_LARGE_FILE_THRESHOLD_BYTES,
                        "large_files": large_files,
                        "message": (
                            "整批尚未写入，也未创建快照或上传资源。"
                            "如需上传这些大文件，请在用户明确同意后以相同参数增加 "
                            "upload_large_files=true 重试。"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )

        if action in {"single_block_replace", "multi_block_replace"}:
            refused = [
                f"[{block.index}] id={block.id} type={display_block_semantic_type(block)}"
                for block in target_blocks
                if display_block_semantic_type(block) in REPLACE_REFUSED_SEMANTIC_TYPES
            ]
            if refused:
                raise tool_error(_ERR_WRONG_TARGET,
                    f"{action} 暂不支持复杂块类型。\n"
                    "处理建议：如需移除目标块，用 delete；如需补充说明，用 insert_before 或 insert_after；"
                    "如需重构复杂块附近内容，请只替换普通文本/标题/代码/表格块。\n"
                    + "\n".join(refused)
                )

        if action == "single_block_replace":
            if len(target_blocks) != 1:
                raise tool_error(_ERR_WRONG_SHAPE,
                    "single_block_replace 只能替换单个块，并保留该块 ID 和块属性。"
                    "当前目标是多个块；请改用 multi_block_replace。注意 multi_block_replace 会重建块，"
                    "旧块 ID 和指向旧块的引用会失效。"
                )
            if markdown_has_multiple_blocks(markdown):
                raise tool_error(_ERR_WRONG_SHAPE,
                    "single_block_replace 的 markdown 必须只生成一个展示块，因为它会复用原块 ID 和块属性。"
                    "当前 markdown 会被思源拆成多个块；请改用 multi_block_replace。"
                    "注意 multi_block_replace 会重建块，旧块 ID 和指向旧块的引用会失效。"
                )

        new_table = ""
        if action == "table_edit":
            target = target_blocks[0]
            if len(target_blocks) != 1:
                raise tool_error(_ERR_WRONG_SHAPE, "table_edit 只能作用于单个普通 Markdown 表格块。范围表格编辑请拆成多次调用。")
            if display_block_semantic_type(target) != "table":
                raise tool_error(_ERR_WRONG_TARGET,
                    f"table_edit 只能作用于 type=table 的普通 Markdown 表格；当前目标为 type={display_block_semantic_type(target)}。"
                    "如果要在该块附近添加表格或说明，请使用 insert_before / insert_after；"
                    "如果要整体替换为普通内容，请使用 multi_block_replace。"
                )
            new_table = apply_table_edit(display_block_source(target), args["table_edit"])

        if target_blocks:
            target_start_pos = block_index_by_id(display_blocks, target_blocks[0].id)
            target_end_pos = block_index_by_id(display_blocks, target_blocks[-1].id)
        else:
            target_start_pos = None
            target_end_pos = None
        previous_anchor = (
            display_blocks[target_start_pos - 1]
            if target_start_pos is not None and target_start_pos > 0
            else None
        )
        next_anchor = (
            display_blocks[target_end_pos + 1]
            if target_end_pos is not None and target_end_pos + 1 < len(display_blocks)
            else None
        )
        last_before_append = display_blocks[-1] if display_blocks else None

        reference_notice = ""
        if action in {"delete", "multi_block_replace"}:
            with ensure_notebooks_open(client, [notebook_id]):
                all_block_rows = client.list_document_blocks(doc_id)
            deleting_ids = expand_deleted_block_ids(
                all_block_rows,
                {block.id for block in target_blocks},
            )
            reference_notice = self._protect_referenced_blocks(client, deleting_ids, args)

        self._create_snapshot_or_raise(client, "siyuan_edit", doc_title)

        if action == "insert_assets":
            resolved_paths: list[str] = []
            rendered_markdowns: list[str] = []
            insertion_attempted = False
            try:
                with ensure_notebooks_open(client, [notebook_id]):
                    succ_map = client.insert_local_assets(
                        doc_id,
                        [item.local_path for item in asset_items],
                        is_upload=True,
                    )
                    resolved_paths = resolve_uploaded_asset_paths(asset_items, succ_map)
                    rendered_markdowns = [
                        render_asset_markdown(item, resolved_path)
                        for item, resolved_path in zip(asset_items, resolved_paths)
                    ]
                    asset_markdown = "\n\n".join(rendered_markdowns)
                    insertion_attempted = True
                    client.insert_block_after(target_blocks[-1].id, asset_markdown)
                    new_display_blocks = build_display_blocks(client, doc_id, include_block_ids=True)

                inserted = blocks_between_anchors(
                    new_display_blocks,
                    target_blocks[-1].id,
                    next_anchor.id if next_anchor else None,
                )
                inserted_source = "\n".join(display_block_source(block) for block in inserted)
                missing_paths = [
                    resolved_path
                    for resolved_path in resolved_paths
                    if resolved_path not in inserted_source
                ]
                if missing_paths:
                    raise tool_error(
                        _ERR_ASSET_INSERT,
                        "写入后的文档中未找到以下思源资源路径："
                        + "；".join(missing_paths),
                    )
            except Exception as exc:
                cleanup_detail = "尚未插入文档块，无需清理文档。"
                if insertion_attempted and resolved_paths:
                    try:
                        with ensure_notebooks_open(client, [notebook_id]):
                            current_blocks = build_display_blocks(client, doc_id, include_block_ids=True)
                            candidates = blocks_between_anchors(
                                current_blocks,
                                target_blocks[-1].id,
                                next_anchor.id if next_anchor else None,
                            )
                            rendered_markdown_set = {
                                markdown.strip() for markdown in rendered_markdowns
                            }
                            inserted_candidates = [
                                block
                                for block in candidates
                                if display_block_source(block).strip()
                                in rendered_markdown_set
                            ]
                            for block in reversed(inserted_candidates):
                                client.delete_block(block.id)
                            after_cleanup = build_display_blocks(client, doc_id, include_block_ids=True)
                        remaining_candidates = blocks_between_anchors(
                            after_cleanup,
                            target_blocks[-1].id,
                            next_anchor.id if next_anchor else None,
                        )
                        remaining_markdown = {
                            display_block_source(block).strip()
                            for block in remaining_candidates
                        }
                        remaining_paths = [
                            resolved_path
                            for resolved_path, markdown in zip(resolved_paths, rendered_markdowns)
                            if markdown.strip() in remaining_markdown
                        ]
                        cleanup_detail = (
                            f"已删除 {len(inserted_candidates)} 个可明确识别为本批插入的文档块。"
                            if not remaining_paths
                            else "文档补偿未完全成功，仍可检测到：" + "；".join(remaining_paths)
                        )
                    except Exception as cleanup_exc:
                        cleanup_detail = f"文档补偿失败：{cleanup_exc}"
                residual_detail = (
                    "思源资源目录中可能保留以下资源；为避免删除去重后被其他文档共用的附件，"
                    "程序未自动删除：" + "；".join(resolved_paths)
                    if resolved_paths
                    else "思源接口可能已处理部分资源，但没有返回可安全识别的完整路径，程序未自动删除附件。"
                )
                raise tool_error(
                    _ERR_ASSET_INSERT if insertion_attempted else _ERR_ASSET_UPLOAD,
                    "附件插入未完成。\n"
                    f"原始错误：{exc}\n"
                    f"{cleanup_detail}\n"
                    f"{residual_detail}\n"
                    "如需恢复，请在思源中使用本次操作前创建的快照手动恢复。",
                ) from exc

            try:
                client.push_msg(f"思源桥：已向「{doc_title}」插入 {len(asset_items)} 个附件")
            except Exception:
                pass
            return json.dumps(
                {
                    "ok": True,
                    "action": "insert_assets",
                    "document": doc_title,
                    "document_id": doc_id,
                    "anchor": {
                        "start_index": target_blocks[-1].index,
                        "start_id": target_blocks[-1].id,
                    },
                    "inserted": [
                        {
                            "local_path": item.local_path,
                            "kind": item.kind,
                            "resolved_path": resolved_path,
                            "name": item.name,
                            "title": item.title,
                            "verified": True,
                        }
                        for item, resolved_path in zip(asset_items, resolved_paths)
                    ],
                    "snapshot_created": True,
                },
                ensure_ascii=False,
                indent=2,
            )

        with ensure_notebooks_open(client, [notebook_id]):
            if action == "append":
                client.append_block(doc_id, markdown)
            elif action == "insert_after":
                client.insert_block_after(target_blocks[-1].id, markdown)
            elif action == "insert_before":
                client.insert_block_before(target_blocks[0].id, markdown)
            elif action == "delete":
                for block in reversed(target_blocks):
                    client.delete_block(block.id)
            elif action == "table_edit":
                self._update_block_preserving_attrs(client, target_blocks[0].id, new_table)
            elif action == "single_block_replace":
                self._update_block_preserving_attrs(client, target_blocks[0].id, markdown)
            elif action == "multi_block_replace":
                client.insert_block_before(target_blocks[0].id, markdown)
                for block in reversed(target_blocks):
                    client.delete_block(block.id)

            new_display_blocks = build_display_blocks(client, doc_id, include_block_ids=True)

        try:
            client.push_msg(f"思源桥：已编辑「{doc_title}」")
        except Exception:
            pass

        parts = [
            "# 文档已编辑",
            "",
            f"文档：{doc_title}（`{doc_id}`）",
            f"action：{action}",
        ]

        if action in {"single_block_replace", "multi_block_replace"}:
            if action == "single_block_replace":
                replaced = [
                    block for block in new_display_blocks
                    if block.id == target_blocks[0].id
                ]
            else:
                replaced = blocks_between_anchors(
                    new_display_blocks,
                    previous_anchor.id if previous_anchor else None,
                    next_anchor.id if next_anchor else None,
                )
                deleted_ids = {block.id for block in target_blocks}
                replaced = [block for block in replaced if block.id not in deleted_ids]
            parts.extend([
                f"已替换 {len(target_blocks)} 个块：{block_range_label(target_blocks)}",
                "",
                "## 原内容",
                "",
                format_display_blocks(target_blocks),
                "",
                "## 新内容",
                "",
                format_display_blocks(replaced),
            ])
        elif action in {"insert_after", "insert_before"}:
            if action == "insert_after":
                inserted = blocks_between_anchors(
                    new_display_blocks,
                    target_blocks[-1].id,
                    next_anchor.id if next_anchor else None,
                )
            else:
                inserted = blocks_between_anchors(
                    new_display_blocks,
                    previous_anchor.id if previous_anchor else None,
                    target_blocks[0].id,
                )
            parts.extend([
                f"锚点：{block_range_label(target_blocks)}",
                "",
                "## 锚点内容",
                "",
                format_display_blocks(target_blocks),
                "",
                "## 插入内容",
                "",
                format_display_blocks(inserted),
            ])
        elif action == "append":
            appended = blocks_between_anchors(
                new_display_blocks,
                last_before_append.id if last_before_append else None,
                None,
            )
            parts.extend([
                "",
                "## 追加内容",
                "",
                format_display_blocks(appended),
            ])
        elif action == "delete":
            current_previous = (
                [block for block in new_display_blocks if previous_anchor and block.id == previous_anchor.id]
            )
            current_next = (
                [block for block in new_display_blocks if next_anchor and block.id == next_anchor.id]
            )
            parts.extend([
                f"已删除 {len(target_blocks)} 个块：{block_range_label(target_blocks)}",
                "",
                "## 已删除内容",
                "",
                format_display_blocks(target_blocks),
                "",
                "## 当前上下文",
                "",
                "（删除位置的前一个块）",
                format_display_blocks(current_previous),
                "",
                "（删除位置现在的块，即原来被删除范围的后一个块）",
                format_display_blocks(current_next),
            ])
        elif action == "table_edit":
            updated_table = [
                block for block in new_display_blocks
                if block.id == target_blocks[0].id
            ]
            parts.extend([
                f"目标：{block_range_label(target_blocks)}",
                "",
                "## 原表格",
                "",
                format_display_block(target_blocks[0]),
                "",
                "## 新表格",
                "",
                format_display_blocks(updated_table),
            ])

        parts.extend([
            "",
            "如需回滚，可通过思源快照手动恢复。",
        ])
        if reference_notice:
            parts.extend(["", f"引用保护：{reference_notice}"])
        return "\n".join(parts)

    def siyuan_doc_manage(self, args: dict[str, Any]) -> str:
        action = str(args.get("action") or "").strip().casefold()
        allowed_actions = {"create_notebook", "rename", "move", "delete", "copy", "export"}
        if action not in allowed_actions:
            raise tool_error(_ERR_INVALID_ENUM, "action 只支持 create_notebook、rename、move、delete、copy、export。")

        if action == "create_notebook":
            notebook_name = str(args.get("notebook_name") or "").strip()
            if not notebook_name:
                raise tool_error(_ERR_MISSING_PARAM, "action=create_notebook 需要 notebook_name。")
            if not bool(args.get("confirmed")):
                raise tool_error(_ERR_NOT_CONFIRMED, "action=create_notebook 需要 confirmed=true。")

            docs = load_docs(self.root)
            privacy = load_privacy_rules(self.root)
            prospective_notebook = {"id": "", "name": notebook_name}
            permission = document_permission(notebook_permission_probe(prospective_notebook), privacy, docs)
            if permission != "read_write":
                raise tool_error(
                    _ERR_NOT_READ_WRITE,
                    "目标笔记本名称受隐私规则限制，不允许创建。",
                )

            client = self._require_active_client()
            existing = [
                notebook
                for notebook in client.list_notebooks()
                if str(notebook.get("name") or "").strip().casefold() == notebook_name.casefold()
            ]
            if existing:
                visible = [
                    notebook
                    for notebook in existing
                    if document_permission(notebook_permission_probe(notebook), privacy, docs) != "hidden"
                ]
                if visible:
                    choices = "\n".join(
                        f"- `{notebook.get('id', '')}` {notebook.get('name', '')}"
                        for notebook in visible
                    )
                    raise tool_error(
                        _ERR_ALREADY_EXISTS,
                        "同名笔记本已存在，拒绝重复创建。请直接使用已有笔记本。\n" + choices,
                    )
                raise tool_error(
                    _ERR_ALREADY_EXISTS,
                    "同名笔记本已存在或不可用，拒绝重复创建。",
                )

            snapshot_status = self._create_snapshot_or_raise(
                client,
                "siyuan_doc_manage",
                f"/{notebook_name}",
            )
            result = client.create_notebook(notebook_name)
            notebook_id = str(result.get("id") or "")
            if not notebook_id:
                created = [
                    notebook
                    for notebook in client.list_notebooks()
                    if str(notebook.get("name") or "").strip().casefold() == notebook_name.casefold()
                ]
                if len(created) == 1:
                    notebook_id = str(created[0].get("id") or "")
            if not notebook_id:
                raise tool_error(_ERR_NB_NOT_FOUND, "思源未返回新笔记本 ID，无法确认创建结果。")

            try:
                client.push_msg(f"思源桥：已创建笔记本「{notebook_name}」")
            except Exception:
                pass

            refresh_ok = False
            try:
                self._refresh_index_with_system_context(client)
                refresh_ok = True
            except Exception:
                pass

            return "\n".join([
                "# 笔记本已创建",
                "",
                f"笔记本：/{notebook_name}",
                f"notebook_id：`{notebook_id}`",
                f"快照：{snapshot_status}",
                "索引：已自动刷新"
                if refresh_ok
                else '索引：自动刷新失败，请手动运行 `siyuan_operate(action="refresh")`',
                f'现在可以使用 `siyuan_create(path="/{notebook_name}/<文档标题>", ...)` 创建文档。',
            ])

        doc = self.resolve_visible_document(args)
        doc_id = str(doc.get("id", ""))
        doc_path = display_document_path(doc)
        notebook_id = str(doc.get("notebook_id", ""))
        source_hpath = normalize_display_path(str(doc.get("hpath", "")))
        source_title = str(doc.get("title") or source_hpath.strip("/").split("/")[-1] or doc_id)
        docs = load_docs(self.root)
        privacy = load_privacy_rules(self.root)
        permission = document_permission(doc, privacy, docs)
        if permission == "hidden":
            raise tool_error(_ERR_DOC_NOT_FOUND, "未找到匹配的可见文档。文档可能已被隐藏、尚未索引，或定位符有误。")

        write_actions = {"rename", "move", "delete"}
        if action in write_actions and permission != "read_write":
            raise tool_error(_ERR_NOT_READ_WRITE, f"当前文档权限为 {permission}，不允许 {action}。")
        if action in write_actions | {"copy"} and not bool(args.get("confirmed")):
            raise tool_error(_ERR_NOT_CONFIRMED, f"action={action} 需要 confirmed=true。")

        client = self._require_active_client()

        if action == "export":
            with ensure_notebooks_open(client, [notebook_id]):
                markdown = client.export_markdown(doc_id)
            exports_dir = self.root / "ai_workspace" / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)
            safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", doc_path.strip("/") or doc_id)

            # Create a self-contained export directory with assets
            export_dir = exports_dir / safe_name
            export_dir.mkdir(parents=True, exist_ok=True)
            attachment_count = extract_attachments(markdown, client, doc_id, self.root)
            attachments_src = attachment_root_dir(self.root, doc_id) / "assets"
            export_assets_dir = export_dir / "assets"
            if attachments_src.exists():
                if export_assets_dir.exists():
                    shutil.rmtree(export_assets_dir)
                shutil.copytree(attachments_src, export_assets_dir)
            export_md_path = export_dir / f"{safe_name}.md"
            export_md_path.write_text(markdown, encoding="utf-8")

            parts = [
                "# 文档已导出",
                "",
                f"文档：{doc_path}（`{doc_id}`）",
                f"格式：Markdown（自包含目录）",
                f"路径：{export_md_path.resolve()}",
            ]
            if attachment_count:
                parts.append(f"附件：{attachment_count} 个已复制到 {export_assets_dir.resolve()}")
            return "\n".join(parts)

        new_title = ""
        target_id = ""
        target_label = ""
        copy_target: CreateTarget | None = None
        copy_title = ""
        copy_parent_id = ""
        reference_notice = ""
        if action == "rename":
            new_title = str(args.get("new_title") or "").strip()
            if not new_title:
                raise tool_error(_ERR_MISSING_PARAM, "action=rename 需要 new_title。")
        elif action == "move":
            target_parent = str(args.get("target_parent") or "").strip()
            if not target_parent:
                raise tool_error(_ERR_MISSING_PARAM, "action=move 需要 target_parent，例如 /Notebook 或 /Notebook/Folder。")
            target_id, target_label = self.resolve_doc_manage_parent(target_parent)
            self._ensure_doc_manage_ancestors_writable(doc, privacy, docs, action="move")
            self._ensure_doc_manage_target_parent_writable(target_label, privacy, docs, action="move")
        elif action == "copy":
            target_path = str(args.get("target_path") or "").strip()
            if not target_path:
                raise tool_error(_ERR_MISSING_PARAM, "action=copy 需要 target_path，例如 /Notebook/Folder/New Doc。")
            copy_title = target_path.strip("/").split("/")[-1]
            if not copy_title:
                raise tool_error(_ERR_MISSING_PARAM, "复制目标标题为空。")
            notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
            visible_docs = filter_documents(load_docs(self.root), privacy)
            copy_target = resolve_create_target({"path": target_path}, notebooks, visible_docs, copy_title)
            target_doc_for_permission = {
                "id": "",
                "notebook_id": copy_target.notebook_id,
                "notebook_name": copy_target.notebook_name,
                "hpath": copy_target.internal_path,
            }
            if document_permission(target_doc_for_permission, privacy, docs) != "read_write":
                raise tool_error(_ERR_NOT_READ_WRITE, "复制目标路径权限不是 read_write，不允许创建副本。")
            if copy_target.existing_docs:
                choices = "\n".join(f"- `{item.get('id', '')}` {display_document_path(item)}" for item in copy_target.existing_docs)
                raise tool_error(_ERR_ALREADY_EXISTS, "复制目标文档已存在，拒绝覆盖。\n" + choices)
            copy_parent = parent_display_path(copy_target.display_path)
            copy_parent_id, _copy_parent_label = self.resolve_doc_manage_parent(copy_parent)
        elif action == "delete":
            delete_subtree = self._ensure_doc_manage_subtree_writable(client, doc, privacy, action="delete")
            deleting_ids = {
                str(item.get("id") or "")
                for item in delete_subtree
                if str(item.get("id") or "")
            }
            with ensure_notebooks_open(client, [notebook_id]):
                for subtree_doc in delete_subtree:
                    subtree_doc_id = str(subtree_doc.get("id") or "")
                    deleting_ids.update(
                        str(block.get("id") or "")
                        for block in client.list_document_blocks(subtree_doc_id)
                        if str(block.get("id") or "")
                    )
            reference_notice = self._protect_referenced_blocks(client, deleting_ids, args)

        snapshot_status = self._create_snapshot_or_raise(client, "siyuan_doc_manage", doc_path)
        sync_status: PostWriteSyncStatus | None = None
        try:
            operation_source_hpath = normalize_display_path(client.get_hpath_by_id(doc_id))
        except Exception:
            operation_source_hpath = source_hpath
        operation_source_title = operation_source_hpath.strip("/").split("/")[-1] or source_title

        if action == "rename":
            with ensure_notebooks_open(client, [notebook_id]):
                client.rename_doc_by_id(doc_id, new_title)
            result_line = f"已重命名为：{new_title}"
            parent_hpath = "/" + "/".join(operation_source_hpath.strip("/").split("/")[:-1]) if "/" in operation_source_hpath.strip("/") else ""
            expected_hpath = normalize_display_path(f"{parent_hpath}/{new_title}")
            sync_status = self._wait_for_hpath(client, doc_id, expected_hpath)

        elif action == "move":
            with ensure_notebooks_open(client, [notebook_id]):
                client.move_docs_by_id([doc_id], target_id)
            result_line = f"已移动到：{target_label}"
            target_parent_hpath = "/" + "/".join(target_label.strip("/").split("/")[1:])
            expected_hpath = normalize_display_path(f"{target_parent_hpath}/{operation_source_title}")
            sync_status = self._wait_for_hpath(client, doc_id, expected_hpath)

        elif action == "delete":
            with ensure_notebooks_open(client, [notebook_id]):
                client.remove_doc_by_id(doc_id)
            result_line = "已删除文档。可通过思源快照手动恢复。"
            sync_status = self._wait_for_deleted_doc(client, doc_id)

        elif action == "copy":
            assert copy_target is not None
            duplicated_id = ""
            with ensure_notebooks_open(client, [notebook_id, copy_target.notebook_id]):
                result = client.duplicate_doc(doc_id)
                duplicated_id = str(result.get("id") or result.get("docID") or result.get("doc_id") or "")
                if not duplicated_id:
                    raise tool_error(_ERR_DUPLICATE_NO_ID, "duplicateDoc 未返回新文档 ID，无法完成复制。")
                client.rename_doc_by_id(duplicated_id, copy_title)
                client.move_docs_by_id([duplicated_id], copy_parent_id)
            result_line = f"已复制到：{copy_target.display_path}（`{duplicated_id}`）"
            sync_status = self._wait_for_hpath(client, duplicated_id, copy_target.internal_path)

        try:
            client.push_msg(f"思源桥：文档管理已完成「{doc_path}」")
        except Exception:
            pass

        refresh_ok = False
        if action != "export":
            try:
                self._refresh_index_with_system_context(client)
                refresh_ok = True
            except Exception:
                pass

        parts = [
            "# 文档管理已完成",
            "",
            f"文档：{doc_path}（`{doc_id}`）",
            f"action：{action}",
            result_line,
            f"快照：{snapshot_status}",
        ]
        if sync_status is not None:
            parts.append(f"路径同步：{sync_status.detail}")
        if action != "export":
            parts.append("索引：已自动刷新" if refresh_ok else "索引：自动刷新失败，请手动运行 `siyuan_operate(action=\"refresh\")`")
        if action == "delete":
            parts.append("如需回滚，可通过思源快照手动恢复。")
        if reference_notice:
            parts.append(f"引用保护：{reference_notice}")
        return "\n".join(parts)

    def _ensure_doc_manage_subtree_writable(
        self,
        client: Any,
        doc: dict[str, Any],
        privacy: PrivacyRules,
        *,
        action: str,
    ) -> list[dict[str, Any]]:
        notebook_id = str(doc.get("notebook_id") or "")
        with ensure_notebooks_open(client, [notebook_id]):
            live_docs = load_live_docs(client)
        indexed = {str(item.get("id") or ""): item for item in live_docs}
        live_doc = indexed.get(str(doc.get("id") or ""), doc)
        subtree = document_subtree(live_doc, live_docs)
        blocked = [
            (item, document_permission(item, privacy, live_docs))
            for item in subtree
            if document_permission(item, privacy, live_docs) != "read_write"
        ]
        if blocked:
            raise tool_error(_ERR_SUBTREE_BLOCKED,
                "权限不足，子文档中存在只读或隐藏文档，不允许删除整个文档树。"
                "请让用户调整隐私规则后重试。"
            )
        return subtree

    def _ensure_doc_manage_ancestors_writable(
        self,
        doc: dict[str, Any],
        privacy: PrivacyRules,
        docs: list[dict[str, Any]],
        *,
        action: str,
    ) -> None:
        current = parent_display_path(display_document_path(doc))
        while current:
            matches = [item for item in docs if display_document_path(item).casefold() == current.casefold()]
            if matches:
                permission = document_permission(matches[0], privacy, docs)
                if permission != "read_write":
                    raise tool_error(_ERR_ANCESTOR_BLOCKED,
                        f"权限不足，该文档的祖先路径权限不是 read_write，不允许 {action}。"
                        "请让用户调整隐私规则后重试。"
                    )
            next_parent = parent_display_path(current)
            if next_parent == current:
                break
            current = next_parent

    def _ensure_doc_manage_target_parent_writable(
        self,
        target_label: str,
        privacy: PrivacyRules,
        docs: list[dict[str, Any]],
        *,
        action: str,
    ) -> None:
        path = normalize_display_path(target_label)
        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
        notebook = next(
            (nb for nb in notebooks if normalize_display_path(str(nb.get("name", ""))).casefold() == path.casefold()),
            None,
        )
        if notebook is not None:
            probe = {
                "id": "",
                "notebook_id": str(notebook.get("id", "")),
                "notebook_name": str(notebook.get("name", "")),
                "hpath": "/__siyuan_bridge_permission_probe__",
            }
            permission = document_permission(probe, privacy, docs)
        else:
            matches = [doc for doc in docs if display_document_path(doc).casefold() == path.casefold()]
            permission = document_permission(matches[0], privacy, docs) if len(matches) == 1 else "hidden"
        if permission != "read_write":
            raise tool_error(_ERR_NOT_READ_WRITE, f"action={action} 的目标父路径权限为 {permission}，不允许写入。")

    def resolve_doc_manage_parent(self, target_parent: str) -> tuple[str, str]:
        path = normalize_display_path(target_parent)
        if not path:
            raise tool_error(_ERR_MISSING_PARAM, "target_parent 不能为空。")
        docs = filter_documents(load_docs(self.root), load_privacy_rules(self.root))
        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
        notebook = next(
            (nb for nb in notebooks if normalize_display_path(str(nb.get("name", ""))).casefold() == path.casefold()),
            None,
        )
        if notebook is not None:
            return str(notebook.get("id", "")), normalize_display_path(str(notebook.get("name", "")))
        matches = [
            doc for doc in docs
            if display_document_path(doc).casefold() == path.casefold()
        ]
        if len(matches) == 1:
            return str(matches[0].get("id", "")), display_document_path(matches[0])
        if len(matches) > 1:
            choices = "\n".join(f"- `{doc.get('id')}` {display_document_path(doc)}" for doc in matches)
            raise tool_error(_ERR_AMBIGUOUS, f"target_parent 存在歧义：\n{choices}")
        raise tool_error(_ERR_PARENT_NOT_FOUND, f"未找到可见 target_parent：{path}")

    def resolve_notebook_id(self, notebook_name: str) -> str:
        notebooks = read_json(self.root / KNOWLEDGE_BASE_DIR / "notebooks.json")
        exact = [item for item in notebooks if str(item.get("name", "")).casefold() == notebook_name.casefold()]
        if len(exact) == 1:
            return str(exact[0]["id"])
        partial = [item for item in notebooks if notebook_name.casefold() in str(item.get("name", "")).casefold()]
        if len(partial) == 1:
            return str(partial[0]["id"])
        if len(exact) + len(partial) > 1:
            raise tool_error(_ERR_AMBIGUOUS, "笔记本名称存在歧义，请使用 notebook_id")
        raise tool_error(_ERR_NB_NOT_FOUND, f"未匹配到可见笔记本：{notebook_name}")

    def siyuan_bridge_feedback(self, args: dict[str, Any]) -> str:
        """Submit feedback to the SiYuan Bridge developer."""
        feedback_type = str(args.get("type", "")).strip()
        if feedback_type not in ("bug", "feature", "idea"):
            raise tool_error(_ERR_INVALID_ENUM, "type must be one of: bug, feature, idea")
        title = str(args.get("title", "")).strip()
        if not title:
            raise tool_error(_ERR_MISSING_PARAM, "title is required")
        description = str(args.get("description", "")).strip()
        if not description:
            raise tool_error(_ERR_MISSING_PARAM, "description is required")
        contact = str(args.get("contact", "")).strip() or None

        endpoint = get_effective_endpoint(self.root)
        proxy = _resolve_proxy(self.root)
        payload: dict[str, str] = {
            "type": feedback_type,
            "title": title,
            "description": description,
        }
        if contact:
            payload["contact"] = contact

        success = _telemetry_submit_feedback(endpoint, proxy, payload)
        if success:
            return "反馈已提交，感谢你的反馈！"
        else:
            return (
                "反馈提交失败，无法连接到反馈端点。请检查 telemetry_endpoint 配置是否正确、"
                "本地代理是否已开启，或稍后重试。你也可以通过 GitHub Issues 提交反馈。"
            )


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def list_live_notebook_names(client: Any) -> dict[str, str]:
    return {
        str(notebook.get("id", "")): str(notebook.get("name", ""))
        for notebook in client.list_notebooks()
        if isinstance(notebook, dict)
    }


def block_document_id(block: dict[str, Any]) -> str:
    block_type = str(block.get("type", ""))
    if block_type in ("d", "NodeDocument"):
        return str(block.get("id") or block.get("rootID") or block.get("root_id") or "")
    return str(block.get("rootID") or block.get("root_id") or block.get("id") or "")


def live_doc_from_block(
    block: dict[str, Any],
    doc_index: dict[str, dict[str, Any]],
    notebook_names: dict[str, str],
) -> dict[str, Any]:
    doc_id = block_document_id(block)
    indexed = doc_index.get(doc_id, {})
    notebook_id = str(block.get("box") or indexed.get("notebook_id") or "")
    hpath = str(block.get("hPath") or block.get("hpath") or indexed.get("hpath") or "")
    title = str(indexed.get("title") or hpath.strip("/").split("/")[-1] or block.get("content") or doc_id)
    return {
        "id": doc_id,
        "notebook_id": notebook_id,
        "notebook_name": str(indexed.get("notebook_name") or notebook_names.get(notebook_id) or notebook_id),
        "hpath": hpath or str(indexed.get("hpath") or title),
        "path": str(block.get("path") or indexed.get("path") or ""),
        "title": title,
        "word_count": indexed.get("word_count", 0),
        "block_count": indexed.get("block_count", 0),
        "updated": str(block.get("updated") or indexed.get("updated") or ""),
    }


def is_live_doc_visible(
    doc: dict[str, Any],
    compiled_ignore: list[dict[str, Any]],
    compiled_allow: list[dict[str, Any]],
) -> bool:
    ignored = any(rule_matches_live_doc(rule, doc) for rule in compiled_ignore)
    allowed = any(rule_matches_live_doc(rule, doc) for rule in compiled_allow)
    return not ignored or allowed


def rule_matches_live_doc(rule: dict[str, Any], doc: dict[str, Any]) -> bool:
    if rule_matches_doc(rule, doc):
        return True
    if str(rule.get("scope") or "").strip().casefold() not in ("document", "subtree"):
        return False
    root_id = str(rule.get("id") or "")
    path = str(doc.get("path") or "")
    return bool(root_id and f"/{root_id}/" in path)


def local_search_text(doc: dict[str, Any]) -> str:
    return " ".join([
        str(doc.get("id", "")),
        str(doc.get("title", "")),
        str(doc.get("hpath", "")),
        str(doc.get("notebook_name", "")),
        str(doc.get("alias", "")),
        str(doc.get("memo", "")),
        " ".join(str(tag) for tag in doc.get("tags", [])),
    ])


def search_terms(query: str, mode: str) -> list[str]:
    if mode == "regex":
        return [query]
    terms = []
    for quoted, word in re.findall(r'"([^"]+)"|(\S+)', query):
        token = quoted or word
        if token.upper() in ("AND", "OR", "NOT"):
            continue
        token = token.strip("*")
        if token:
            terms.append(token)
    return terms


def query_matches(text: str, query: str) -> bool:
    folded = text.casefold()
    parts = re.split(r"\s+OR\s+", query, flags=re.IGNORECASE)
    return any(query_part_matches(folded, part) for part in parts)


def query_part_matches(folded_text: str, query: str) -> bool:
    required: list[str] = []
    denied: list[str] = []
    negate = False
    for raw in re.findall(r'"[^"]+"|\S+', query):
        token = raw.strip()
        upper = token.upper()
        if upper == "AND":
            continue
        if upper == "NOT":
            negate = True
            continue
        if negate:
            denied.append(token)
            negate = False
        else:
            required.append(token)
    return all(query_token_matches(folded_text, token) for token in required) and not any(
        query_token_matches(folded_text, token) for token in denied
    )


def query_token_matches(folded_text: str, token: str) -> bool:
    text = token.strip('"').casefold()
    if text.endswith("*"):
        text = text[:-1]
    return bool(text and text in folded_text)


def merge_search_results(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            doc_id = str(item.get("id", ""))
            if not doc_id:
                continue
            if doc_id in merged:
                existing = merged[doc_id]
                if item.get("snippet") and not existing.get("snippet"):
                    existing["snippet"] = item["snippet"]
                if item.get("source") and item["source"] not in str(existing.get("source", "")):
                    existing["source"] = f"{existing.get('source')}, {item['source']}"
            else:
                merged[doc_id] = dict(item)
    results = list(merged.values())
    results.sort(key=lambda r: (str(r.get("notebook_name", "")).casefold(), str(r.get("hpath", "")).casefold()))
    return results


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": "siyuan_start",
            "description": "Read the plugin-maintained system notebook, load and merge all registered system documents, refresh the safe index, and return the mandatory startup packet: notebook overview table, Workspace Index, MCP Usage Guide, and User Preferences. This tool never creates, updates, migrates, or registers system documents. Missing non-privacy documents produce a warning and startup continues; if all registered Privacy Rules documents are missing, startup stops and asks the user to disable and re-enable the plugin. Always call this first.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "siyuan_operate",
            "description": "Run read-only or maintenance operations. action=refresh refreshes the safe local SiYuan index without cleaning ai_workspace. action=sync triggers SiYuan's built-in default sync. action=check_references checks standard block references, recognized embed-block references, and siyuan:// block links targeting one visible document and its blocks; the target document is detailed while descendant documents are summarized. To import a local Markdown file as a new document or into an existing document, use siyuan_create or siyuan_edit with markdown_file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["refresh", "sync", "check_references"], "description": "refresh = update the local safe index. sync = trigger SiYuan built-in default sync. check_references = read-only reference detection for one document."},
                    "timeout_seconds": {"type": "integer", "default": 10, "description": "For action=sync only. How long to wait for SiYuan built-in sync to return, 5-120 seconds. Does not change SiYuan sync behavior."},
                    "document": {"type": "string", "description": "For action=check_references. Preferred document path including notebook name. Existing unique-title and unique-partial locator compatibility is preserved."},
                    "document_id": {"type": "string", "description": "For action=check_references. Document ID fallback when the path is ambiguous or unavailable. A body block ID is rejected."},
                    "limit": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "enum": ["none"]},
                        ],
                        "default": 10,
                        "description": "For action=check_references. Maximum visible source documents and visible referenced descendant documents to display. No integer maximum; use \"none\" for all. Totals are never limited.",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_list",
            "description": "List visible notebooks or one level of visible documents. No arguments or path=/ lists notebooks. Provide path=/Notebook or /Notebook/Folder to list only direct child documents at that path. Each row returns effective permission (read_write/read_only), a full readable document path for siyuan_read/siyuan_edit, plus document_id fallback, word count, block count, update date, and descendant document count. Hidden items are not listed. Results are paginated with offset/limit. notebook_id/notebook_name are compatibility shortcuts for path=/Notebook.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Readable path to list one level under, e.g. /Notebook or /Notebook/Folder. Omit or use / to list all notebooks."},
                    "limit": {"type": "integer", "default": 100, "description": "Maximum direct children to return, 1-500."},
                    "offset": {"type": "integer", "default": 0, "description": "Pagination offset within the direct children of path."},
                    "notebook_id": {"type": "string", "description": "Compatibility shortcut. Lists the root level of this notebook."},
                    "notebook_name": {"type": "string", "description": "Compatibility shortcut. Lists the root level of this notebook."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_find",
            "description": "Search the SiYuan knowledge base through SiYuan search APIs, then apply privacy rules before returning results. Temporarily opens closed notebooks while searching and restores them afterwards. Supports 3 modes: query (space-separated terms use AND logic by default; also supports explicit AND/OR/NOT, phrases, and prefix*), regex, and sql (direct SQL, requires admin). Scope: headings (document titles + headings, default) or full (all block text).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search query. For query mode: space-separated terms use AND logic by default; explicit AND/OR/NOT, quoted phrases, and prefix* are supported. For regex mode: Go RE2 regex. For sql mode: raw SQL statement."},
                    "mode": {"type": "string", "enum": ["query", "regex", "sql"], "default": "query", "description": "Search mode. Defaults to query."},
                    "scope": {"type": "string", "enum": ["headings", "full"], "default": "headings", "description": "headings = document titles and outline headings only. full = all block content."},
                    "notebooks": {"description": "Notebook ID or list of IDs to scope the search. 'ALL' (default) searches all notebooks."},
                    "limit": {"type": "integer", "default": 20, "description": "Maximum document results."},
                    "max_snippets_per_doc": {"type": "integer", "default": DEFAULT_SNIPPETS_PER_DOC, "description": "Maximum matching blocks to display per document. The result still reports the total matching block count."},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_read",
            "description": "Read a visible SiYuan document as Markdown. Prefer document path including notebook name, e.g. /Notebook/Folder/Doc; use document_id only as fallback. Always returns the document outline and one complete block window. Set include_block_ids=true before any siyuan_edit call to get exact [index] id type targets. Normal reading keeps Markdown clean and hides block IDs.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "description": "Document path including notebook name, e.g. /Notebook/Folder/Doc. Preferred for reading and editing workflows."},
                    "document_id": {"type": "string", "description": "Document id fallback when path is ambiguous or unavailable."},
                    "block_start": {"type": "integer", "default": 1, "description": "Starting display block index (1-based). Default 1 reads from the first block."},
                    "block_limit": {"type": "integer", "default": DEFAULT_BLOCK_LIMIT, "description": "Maximum display blocks to return in this window, 1–1000."},
                    "token_budget": {"type": "integer", "default": DEFAULT_TOKEN_BUDGET, "description": "Estimated token ceiling for this window. Blocks stop before exceeding budget (at least one block always returned)."},
                    "include_block_ids": {"type": "boolean", "default": False, "description": "Enable reference reading for editing: each block is shown as [index] id=... type=... followed by content. Use these exact values for siyuan_edit start_index/start_id."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_create",
            "description": "Create or write a SiYuan document. Prefer path as the full readable path including notebook name, e.g. /Notebook/Folder/Doc; the server resolves the notebook ID and internal hpath. If the notebook name is ambiguous, use notebook_id plus an internal path like /Folder/Doc. Creates a SiYuan workspace snapshot before writing. After writing, waits for SiYuan to expose the target path and refreshes the safe index. Existing target behavior is controlled by if_exists: reject refuses by default, overwrite clears all blocks in the existing document and rewrites it while preserving the document ID, create_new asks SiYuan to create another same-name document. overwrite checks backlinks for every disappearing body block and refuses by default. To import a local Markdown file as a new document, pass markdown_file (an absolute path) instead of markdown; markdown and markdown_file are mutually exclusive, and only the file's text is imported (embedded images/assets are not uploaded).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "description": "Optional notebook ID. Required only when path is an internal notebook path or when the notebook name in a full path is ambiguous."},
                    "title": {"type": "string", "description": "Document title."},
                    "path": {"type": "string", "description": "Preferred: full readable path /Notebook/Folder/Doc. With notebook_id, legacy internal path /Folder/Doc is also accepted. If omitted, notebook_id is required and path defaults to /<title> inside that notebook."},
                    "markdown": {"type": "string", "description": "Markdown content to write. Mutually exclusive with markdown_file: provide exactly one."},
                    "markdown_file": {"type": "string", "description": "Absolute path to a local Markdown file whose content is imported as the document body. Mutually exclusive with markdown: provide exactly one. Only the text is imported; images and other assets referenced in the file are not uploaded (use siyuan_edit insert_assets for those)."},
                    "if_exists": {"type": "string", "enum": ["reject", "overwrite", "create_new"], "default": "reject", "description": "Behavior when the target path already exists. reject refuses and explains options. overwrite clears all existing blocks and appends markdown, preserving document ID. create_new creates another same-name document."},
                    "reference_policy": {"type": "string", "enum": ["reject", "break"], "default": "reject", "description": "For overwrite only. reject refuses when any disappearing block ID is referenced. Use break only after the user explicitly confirms that those reported references may be broken."},
                    "confirmed": {"type": "boolean", "description": "Must be true. Writing to SiYuan requires explicit user approval."},
                },
                "required": ["title", "confirmed"],
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_edit",
            "description": "Edit a visible SiYuan document by document path plus reference-read block index and block ID. Requires confirmed=true and creates a SiYuan workspace snapshot before writing. Use siyuan_read(include_block_ids=true) first to get start_index/start_id. Actions: single_block_replace = one existing block -> one block, uses updateBlock, preserves the target block ID and block attrs, so existing block references stay valid. multi_block_replace = one or more existing blocks -> one or more new blocks, inserts new markdown then deletes old blocks, so old block IDs/attrs are not preserved. multi_block_replace and delete check backlinks for every disappearing block ID and refuse by default. insert_after/insert_before do not modify the anchor block. append adds to document end. table_edit edits one normal Markdown table block. insert_assets uploads one or more local files/folders through SiYuan's native asset API and inserts their links after one anchor. For actions that take markdown, pass markdown_file (an absolute path) instead of markdown to import a local Markdown file's content; markdown and markdown_file are mutually exclusive, and only text is imported.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "description": "Document path including notebook name, e.g. /Notebook/Folder/Doc. If ambiguous, use document_id instead."},
                    "document_id": {"type": "string", "description": "Optional document id fallback when document path is ambiguous."},
                    "action": {"type": "string", "enum": ["single_block_replace", "multi_block_replace", "insert_after", "insert_before", "append", "delete", "table_edit", "insert_assets"], "description": "Choose single_block_replace only when replacing exactly one block with exactly one block and preserving its block ID matters. Choose multi_block_replace when replacing a range or when the new markdown may create multiple blocks; old block IDs and references will be invalidated. Choose insert_assets to insert local files/folders after one existing anchor."},
                    "start_index": {"type": "integer", "description": "Global display block index from reference reading. Required except append."},
                    "start_id": {"type": "string", "description": "Block ID from reference reading. Required except append."},
                    "end_index": {"type": "integer", "description": "Inclusive global display block index for multi_block_replace/delete range operations."},
                    "end_id": {"type": "string", "description": "Inclusive end block ID for multi_block_replace/delete range operations."},
                    "markdown": {"type": "string", "description": "Markdown to insert or replace with. For single_block_replace this must render as exactly one display block. For multi_block_replace it may render as one or more new blocks. Mutually exclusive with markdown_file."},
                    "markdown_file": {"type": "string", "description": "Absolute path to a local Markdown file whose content is used instead of markdown, for actions that take markdown (single_block_replace, multi_block_replace, insert_after, insert_before, append). Mutually exclusive with markdown. Only text is imported; embedded images/assets are not uploaded."},
                    "reference_policy": {"type": "string", "enum": ["reject", "break"], "default": "reject", "description": "For delete and multi_block_replace only. reject refuses when any disappearing block ID is referenced. Use break only after the user explicitly confirms that those reported references may be broken."},
                    "assets": {
                        "type": "array",
                        "minItems": 1,
                        "description": "Required for action=insert_assets. Every item is inserted after the same start_index/start_id anchor, in array order. Duplicate base filenames in one batch are rejected; split them into separate calls.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_path": {"type": "string", "description": "Absolute path on the computer running this MCP. Files are uploaded into SiYuan; folders become local file:// links and are not copied recursively."},
                                "name": {"type": "string", "description": "Visible body name: image alt text, or file/folder link anchor text. This is not the image caption. If omitted or blank, images use the original filename without extension; files use the full filename; folders use the directory name."},
                                "title": {"type": "string", "description": "Optional Markdown title. For images SiYuan displays it as the caption below the image; for files/folders it is usually only a hover tooltip. This is not the visible file link name. Omit or leave blank to generate no title."},
                            },
                            "required": ["local_path"],
                            "additionalProperties": False,
                        },
                    },
                    "upload_large_files": {"type": "boolean", "default": False, "description": "For action=insert_assets only. Allow ordinary files larger than 20 MB. Default false pauses the whole batch before snapshot/upload and asks for explicit user approval; folders are not size-scanned."},
                    "table_edit": {
                        "type": "object",
                        "description": "Required for action=table_edit on a normal Markdown table block. Use the table coordinate view from siyuan_read(include_block_ids=true): row=0 is header, row>=1 are data rows, column_index is 1-based.",
                        "properties": {
                            "operation": {"type": "string", "enum": ["set_cell", "insert_row", "delete_row", "insert_column", "delete_column", "insert_row_before", "insert_row_after"], "description": "Prefer set_cell, insert_row, delete_row, insert_column, delete_column. insert_row_before/insert_row_after are legacy aliases."},
                            "cell": {"type": "object", "description": "Single cell edit for operation=set_cell. Fields: row, column_index or column, value, optional expected_old_value."},
                            "cells": {"type": "array", "description": "Multiple cell edits for operation=set_cell. Each item has row, column_index or column, value, optional expected_old_value."},
                            "row": {"type": "integer", "description": "Table row coordinate. row=0 is header; row>=1 are data rows. delete_row cannot delete row=0."},
                            "column": {"type": "string", "description": "Legacy column name fallback. Prefer column_index from the reference-reading coordinate view."},
                            "column_index": {"type": "integer", "description": "1-based column number from the reference-reading coordinate view."},
                            "position": {"type": "string", "enum": ["before", "after"], "description": "Required for insert_row and insert_column."},
                            "value": {"type": "string", "description": "Legacy single-cell value for set_cell when not using cell/cells."},
                            "values": {"description": "For insert_row: row values as object keyed by header or array in column order. For insert_column: array where values[0] is header and the rest are data rows."},
                            "expected_old_value": {"type": "string", "description": "Optional old cell value guard for legacy top-level set_cell."},
                        },
                        "additionalProperties": False,
                    },
                    "confirmed": {"type": "boolean", "description": "Must be true. Editing SiYuan documents requires explicit user approval."},
                },
                "required": ["action", "confirmed"],
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_doc_manage",
            "description": "Manage SiYuan notebooks and visible documents at the document-tree level, not document body editing. Actions: create_notebook, rename, move, delete, copy, export. create_notebook requires notebook_name and confirmed=true, creates a workspace snapshot, rejects duplicate names, and refreshes the safe index. It does not create a document automatically. copy/export are allowed for readable documents. rename/move/delete require read_write permission, confirmed=true, and create a SiYuan workspace snapshot before writing. delete removes a document subtree, not a notebook; it is rejected if any descendant is not read_write and checks backlinks for every document/block ID that would disappear. move preserves the moved subtree but is rejected if the source document inherits restrictions from any non-read_write ancestor or if the target parent is not read_write. copy uses SiYuan duplicateDoc for the source document only, requires target_path and confirmed=true, then renames/moves the duplicate. After writes, refreshes the safe index. export writes Markdown to ai_workspace/exports and does not modify SiYuan.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "document": {"type": "string", "description": "Document path including notebook name, e.g. /Notebook/Folder/Doc. Preferred."},
                    "document_id": {"type": "string", "description": "Document id fallback when path is ambiguous or unavailable."},
                    "action": {"type": "string", "enum": ["create_notebook", "rename", "move", "delete", "copy", "export"], "description": "Notebook/document management action. create_notebook is the only notebook-level action; delete removes documents only."},
                    "notebook_name": {"type": "string", "description": "Required for action=create_notebook. Exact name of the new notebook."},
                    "new_title": {"type": "string", "description": "Required for action=rename."},
                    "target_parent": {"type": "string", "description": "Required for action=move. Visible target notebook or parent document path, e.g. /Notebook or /Notebook/Folder."},
                    "target_path": {"type": "string", "description": "Required for action=copy. Full readable target path /Notebook/Folder/New Doc. The target path must not already exist and must be read_write."},
                    "reference_policy": {"type": "string", "enum": ["reject", "break"], "default": "reject", "description": "For action=delete only. reject refuses when any disappearing document/block ID is referenced. Use break only after the user explicitly confirms that those reported references may be broken."},
                    "confirmed": {"type": "boolean", "description": "Required for create_notebook/rename/move/delete/copy. Not required for export."},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
        {
            "name": "siyuan_bridge_feedback",
            "description": "Submit feedback about SiYuan Bridge directly through the AI conversation. Use this to report bugs, request features, or share ideas. This does NOT modify SiYuan notes, does NOT require confirmed=true, and works even when SiYuan is not running (as long as a telemetry endpoint is configured). The feedback is sent to the SiYuan Bridge developer.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["bug", "feature", "idea"],
                        "description": "Feedback type: bug = problem report, feature = feature request, idea = suggestion or general idea.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short summary of the feedback (required).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of the issue, request, or idea (required).",
                    },
                    "contact": {
                        "type": "string",
                        "description": "Optional contact information (email, GitHub handle, etc.) for follow-up.",
                    },
                },
                "required": ["type", "title", "description"],
                "additionalProperties": False,
            },
        },
    ]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def find_markdown_images(markdown: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown)


def attachment_root_dir(workspace_root: Path, doc_id: str) -> Path:
    return workspace_root / "ai_workspace" / "attachments" / doc_id


def rewrite_local_asset_links(markdown: str, doc_id: str, workspace_root: Path) -> str:
    """Rewrite SiYuan-relative asset links to extracted absolute local paths."""
    assets_dir = attachment_root_dir(workspace_root, doc_id) / "assets"

    def replace(match: re.Match[str]) -> str:
        filename = match.group(1)
        return f"]({(assets_dir / filename).resolve().as_posix()})"

    return re.sub(r"\]\(assets/([^)]+)\)", replace, markdown)


def extract_attachments(markdown: str, client: SiYuanClient, doc_id: str, workspace_root: Path) -> int:
    """Extract all assets (images, PDF, etc.) referenced in markdown to ai_workspace/attachments/<doc_id>/.
    Preserves the original assets/ directory structure. Returns count of successfully extracted files."""
    assets = re.findall(r"\]\(assets/([^)]+)\)", markdown)
    if not assets:
        return 0

    dest_dir = attachment_root_dir(workspace_root, doc_id) / "assets"
    dest_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for filename in assets:
        try:
            data = client.get_asset(f"assets/{filename}")
            filepath = dest_dir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(data)
            count += 1
        except Exception:
            pass

    return count


def make_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def make_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
