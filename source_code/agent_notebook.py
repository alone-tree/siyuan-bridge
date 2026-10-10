from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import SiYuanClient
from .i18n import (
    WORKSPACE_INDEX_PLACEHOLDERS,
    match_doc_key,
    match_notebook_name,
    resolve_language,
)
from .ignore import PrivacyRules, parse_privacy_rules_markdown
from .system_templates import markdown_sha256


SYSTEM_DOCUMENT_KEYS = (
    "ai_guide",
    "workspace_index",
    "about",
    "privacy_rules",
)

# Built-in guide assets shipped with the bridge. They are code, not user content:
# updates replace them together with the plugin, never through the plugin data area.
GUIDE_DIR = Path("templates") / "guides"
MCP_USAGE_GUIDE_FILES = {
    "zh-CN": "mcp-usage-guide.zh-CN.md",
    "en": "mcp-usage-guide.en.md",
}
WORKSPACE_INDEX_GUIDE_FILES = {
    "zh-CN": "workspace-index-guide.zh-CN.md",
    "en": "workspace-index-guide.en.md",
}


def guide_asset_path(root: Path, files: dict[str, str], language: str) -> Path:
    filename = files.get(language) or files["zh-CN"]
    return (root / GUIDE_DIR / filename).absolute()


def load_guide_asset(root: Path, files: dict[str, str], language: str) -> str:
    try:
        return guide_asset_path(root, files, language).read_text(encoding="utf-8")
    except OSError:
        return ""


class PrivacyRulesUnavailableError(RuntimeError):
    """Raised when no registered Privacy Rules document remains available."""


@dataclass(frozen=True)
class AgentNotebookState:
    language: str
    notebook_id: str
    notebook_name: str
    document_ids: dict[str, tuple[str, ...]]
    ai_guide_markdown: str
    workspace_index_markdown: str
    privacy_rules: PrivacyRules
    mcp_usage_guide_markdown: str = ""
    workspace_index_guide_path: str = ""
    workspace_index_updated: str = ""
    workspace_index_is_placeholder: bool = False
    missing_document_keys: tuple[str, ...] = ()

    @property
    def privacy_rules_doc_ids(self) -> tuple[str, ...]:
        return self.document_ids.get("privacy_rules", ())


def load_agent_notebook(
    client: SiYuanClient,
    root: Path,
    config_language: str | None = None,
) -> AgentNotebookState:
    """Locate the system notebook and its documents by name; read-only."""
    language = resolve_language(config_language)
    matches = []
    for item in client.list_notebooks():
        matched_language = match_notebook_name(str(item.get("name") or ""))
        if matched_language:
            matches.append((item, matched_language))
    if not matches:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())
    # Multiple same-name notebooks: the first one in lsNotebooks order wins.
    notebook, notebook_language = matches[0]
    notebook_id = str(notebook.get("id") or "")
    notebook_name = str(notebook.get("name") or "")
    if not notebook_id:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())
    if notebook_language:
        language = notebook_language

    live_docs = _list_system_docs(client, notebook_id)
    live_by_id = {str(doc.get("id") or ""): doc for doc in live_docs}

    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in SYSTEM_DOCUMENT_KEYS}
    for doc in live_docs:
        key = match_doc_key(str(doc.get("hpath") or ""))
        if key in grouped:
            grouped[key].append(doc)

    document_ids: dict[str, tuple[str, ...]] = {}
    missing: list[str] = []
    for key in SYSTEM_DOCUMENT_KEYS:
        document_ids[key] = tuple(
            str(doc.get("id") or "") for doc in grouped[key] if doc.get("id")
        )
        if not grouped[key]:
            missing.append(key)

    if not grouped["privacy_rules"]:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())

    markdown_cache: dict[str, str] = {}

    def markdown_for(key: str) -> list[str]:
        values: list[str] = []
        for doc in grouped[key]:
            doc_id = str(doc.get("id") or "")
            if doc_id not in markdown_cache:
                markdown_cache[doc_id] = _export_markdown(client, notebook_id, doc_id)
            values.append(markdown_cache[doc_id])
        return values

    privacy_rules = _merge_privacy_rules(markdown_for("privacy_rules"))
    workspace_docs = grouped["workspace_index"]
    workspace_markdown = markdown_for("workspace_index")
    workspace_updated = max(
        (
            str(live_by_id.get(str(doc.get("id") or ""), {}).get("updated") or "")
            for doc in workspace_docs
        ),
        default="",
    )
    placeholder_hashes = {
        markdown_sha256(markdown)
        for markdown in WORKSPACE_INDEX_PLACEHOLDERS.values()
    }
    workspace_is_placeholder = bool(workspace_docs) and all(
        markdown_sha256(markdown) in placeholder_hashes
        for markdown in workspace_markdown
    )
    return AgentNotebookState(
        language=language,
        notebook_id=notebook_id,
        notebook_name=notebook_name,
        document_ids=document_ids,
        ai_guide_markdown=_merge_markdown(markdown_for("ai_guide")),
        workspace_index_markdown=_merge_markdown(workspace_markdown),
        privacy_rules=privacy_rules,
        mcp_usage_guide_markdown=load_guide_asset(root, MCP_USAGE_GUIDE_FILES, language),
        workspace_index_guide_path=str(
            guide_asset_path(root, WORKSPACE_INDEX_GUIDE_FILES, language)
        ),
        workspace_index_updated=workspace_updated,
        workspace_index_is_placeholder=workspace_is_placeholder,
        missing_document_keys=tuple(missing),
    )


def _list_system_docs(
    client: SiYuanClient, notebook_id: str
) -> list[dict[str, Any]]:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        return client.query_sql(
            "SELECT id, box, path, hpath, markdown, content, updated "
            f"FROM blocks WHERE type='d' AND box='{_sql(notebook_id)}'"
        )


def _export_markdown(client: SiYuanClient, notebook_id: str, doc_id: str) -> str:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        return client.export_markdown(doc_id)


def _merge_markdown(values: list[str]) -> str:
    return "\n\n---\n\n".join(value.strip() for value in values if value.strip())


def _merge_privacy_rules(values: list[str]) -> PrivacyRules:
    ignore: list[dict[str, Any]] = []
    allow: list[dict[str, Any]] = []
    permissions: list[dict[str, Any]] = []
    for markdown in values:
        rules = parse_privacy_rules_markdown(markdown)
        ignore.extend(rules.ignore)
        allow.extend(rules.allow)
        permissions.extend(rules.permissions)
    return PrivacyRules(ignore=ignore, allow=allow, permissions=permissions)


def _privacy_rules_missing_message() -> str:
    return (
        "隐私规则文档缺失，思源桥已停止访问知识库。"
        "请在思源中禁用并重新启用“思源桥”插件，等待插件重新创建隐私规则文档后，"
        "再调用 siyuan_start。"
    )


def is_system_document(hpath: str) -> bool:
    return match_doc_key(hpath) is not None


def collect_system_notebook_ids(client: SiYuanClient) -> frozenset[str]:
    """Return live IDs of notebooks whose names match the system notebook."""
    return frozenset(
        str(item.get("id") or "")
        for item in client.list_notebooks()
        if str(item.get("id") or "")
        and match_notebook_name(str(item.get("name") or ""))
    )


def is_privacy_rules_document(
    hpath: str,
    *,
    notebook_id: str = "",
    system_notebook_ids: frozenset[str] = frozenset(),
) -> bool:
    """Hard-isolate Privacy Rules only inside the system notebook.

    Same-name documents in other notebooks stay ordinary user documents.
    """
    return bool(
        notebook_id
        and notebook_id in system_notebook_ids
        and match_doc_key(hpath) == "privacy_rules"
    )


def is_system_notebook_name(name: str) -> bool:
    return match_notebook_name(name) is not None


def _sql(value: str) -> str:
    return str(value).replace("'", "''")
