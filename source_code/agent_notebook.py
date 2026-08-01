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
from .system_state import active_system_ids, document_entries, load_system_state


SYSTEM_DOCUMENT_KEYS = (
    "ai_guide",
    "mcp_usage_guide",
    "workspace_index_guide",
    "workspace_index",
    "about",
    "privacy_rules",
)


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
    """Read the plugin-maintained system registry without repairing or writing it."""
    language = resolve_language(config_language)
    state = load_system_state(root)
    active_key = str(state.get("active_workspace_key") or "")
    workspace = state.get("workspaces", {}).get(active_key, {})
    if not isinstance(workspace, dict):
        workspace = {}
    notebook = workspace.get("system_notebook", {})
    notebook_id = str(notebook.get("id") or "") if isinstance(notebook, dict) else ""
    notebook_name = str(notebook.get("name") or "") if isinstance(notebook, dict) else ""
    if not notebook_id:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())

    live_notebooks = {
        str(item.get("id") or ""): item for item in client.list_notebooks()
    }
    live_notebook = live_notebooks.get(notebook_id)
    if not live_notebook:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())
    notebook_name = str(live_notebook.get("name") or notebook_name)
    notebook_language = match_notebook_name(notebook_name)
    if notebook_language:
        language = notebook_language

    live_docs = _list_system_docs(client, notebook_id)
    live_by_id = {str(doc.get("id") or ""): doc for doc in live_docs}
    registry = workspace.get("documents", {})
    if not isinstance(registry, dict):
        registry = {}

    valid_entries: dict[str, list[dict[str, Any]]] = {}
    document_ids: dict[str, tuple[str, ...]] = {}
    missing: list[str] = []
    for key in SYSTEM_DOCUMENT_KEYS:
        entries = [
            entry
            for entry in document_entries(registry.get(key))
            if str(entry.get("id") or "") in live_by_id
        ]
        valid_entries[key] = entries
        document_ids[key] = tuple(str(entry["id"]) for entry in entries)
        if not entries:
            missing.append(key)

    if not valid_entries["privacy_rules"]:
        raise PrivacyRulesUnavailableError(_privacy_rules_missing_message())

    markdown_cache: dict[str, str] = {}

    def markdown_for(key: str) -> list[str]:
        values: list[str] = []
        for entry in valid_entries[key]:
            doc_id = str(entry["id"])
            if doc_id not in markdown_cache:
                markdown_cache[doc_id] = _export_markdown(client, notebook_id, doc_id)
            values.append(markdown_cache[doc_id])
        return values

    privacy_rules = _merge_privacy_rules(markdown_for("privacy_rules"))
    workspace_entries = valid_entries["workspace_index"]
    workspace_markdown = markdown_for("workspace_index")
    workspace_updated = max(
        (
            str(live_by_id[str(entry["id"])].get("updated") or "")
            for entry in workspace_entries
        ),
        default="",
    )
    placeholder_hashes = {
        markdown_sha256(markdown)
        for markdown in WORKSPACE_INDEX_PLACEHOLDERS.values()
    }
    workspace_is_placeholder = bool(workspace_entries) and all(
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
        mcp_usage_guide_markdown=_merge_markdown(markdown_for("mcp_usage_guide")),
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


def is_privacy_rules_document(
    hpath: str,
    *,
    root: Path | None = None,
    document_id: str = "",
    notebook_id: str = "",
) -> bool:
    if root is not None:
        system_notebook_id, document_ids = active_system_ids(root)
        privacy_rules_ids = document_ids.get("privacy_rules", set())
        if document_id and document_id in privacy_rules_ids:
            return True
        return bool(
            system_notebook_id
            and notebook_id == system_notebook_id
            and match_doc_key(hpath) == "privacy_rules"
        )
    return match_doc_key(hpath) == "privacy_rules"


def is_system_notebook_name(name: str) -> bool:
    return match_notebook_name(name) is not None


def _sql(value: str) -> str:
    return str(value).replace("'", "''")
