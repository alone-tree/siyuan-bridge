from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import SiYuanClient
from .i18n import (
    ABOUT_TEMPLATES,
    AI_GUIDE_TEMPLATES,
    LEGACY_AI_GUIDE_TEMPLATES,
    LEGACY_DOC_NAMES,
    PRIVACY_RULES_TEMPLATES,
    SYSTEM_DOC_NAMES,
    WORKSPACE_INDEX_PLACEHOLDERS,
    all_notebook_names,
    get_doc_name,
    get_notebook_name,
    match_doc_key,
    match_notebook_name,
    resolve_language,
)
from .ignore import PrivacyRules, parse_privacy_rules_markdown
from .system_state import (
    active_system_ids,
    get_workspace_state,
    load_system_state,
    save_system_state,
    update_workspace_metadata,
)
from .system_templates import (
    SystemTemplate,
    load_system_template,
    markdown_sha256,
    source_sha256,
)


@dataclass(frozen=True)
class AgentNotebookState:
    language: str
    notebook_id: str
    notebook_name: str
    ai_guide_doc_id: str
    ai_guide_markdown: str
    workspace_index_doc_id: str
    workspace_index_markdown: str
    about_doc_id: str
    privacy_rules_doc_id: str
    privacy_rules: PrivacyRules
    mcp_usage_guide_doc_id: str = ""
    mcp_usage_guide_markdown: str = ""
    workspace_index_guide_doc_id: str = ""
    workspace_index_guide_markdown: str = ""
    workspace_index_updated: str = ""
    workspace_index_is_placeholder: bool = False


def ensure_agent_notebook(
    client: SiYuanClient,
    root: Path,
    config_language: str | None = None,
    *,
    detect_existing_language: bool = True,
) -> AgentNotebookState:
    """Reconcile the system notebook and all six fixed documents."""
    language = resolve_language(config_language)
    state_cache = load_system_state(root)
    active_workspace_key = str(state_cache.get("active_workspace_key") or "")
    workspace_cache = state_cache.get("workspaces", {}).get(active_workspace_key, {})
    if not isinstance(workspace_cache, dict):
        workspace_cache = {}
    cached_notebook = workspace_cache.get("system_notebook", {})
    cached_notebook_id = (
        str(cached_notebook.get("id") or "")
        if isinstance(cached_notebook, dict)
        else ""
    )

    notebook_id, notebook_name, notebook_language = _ensure_system_notebook(
        client,
        language,
        detect_existing_language,
        cached_notebook_id=cached_notebook_id,
    )
    effective_language = notebook_language or language
    current_workspace_key = notebook_id
    workspace_cache = update_workspace_metadata(
        state_cache,
        current_workspace_key,
        notebook_id=notebook_id,
        notebook_name=notebook_name,
    )
    document_cache = workspace_cache.setdefault("documents", {})
    docs = _list_system_docs(client, notebook_id)

    ai_guide = _ensure_ai_preferences(
        client, notebook_id, docs, effective_language, document_cache
    )
    about = _ensure_about(
        client, notebook_id, docs, effective_language, document_cache
    )
    privacy_rules = _ensure_privacy_rules(
        client, notebook_id, docs, effective_language, document_cache
    )
    mcp_usage_guide = _ensure_managed_guide(
        client,
        root,
        notebook_id,
        docs,
        effective_language,
        document_cache,
        "mcp_usage_guide",
    )
    workspace_index_guide = _ensure_managed_guide(
        client,
        root,
        notebook_id,
        docs,
        effective_language,
        document_cache,
        "workspace_index_guide",
    )
    workspace_index = _ensure_workspace_index(
        client, notebook_id, docs, effective_language, document_cache
    )

    save_system_state(root, state_cache)
    return AgentNotebookState(
        language=effective_language,
        notebook_id=notebook_id,
        notebook_name=notebook_name,
        ai_guide_doc_id=ai_guide["id"],
        ai_guide_markdown=ai_guide["markdown"],
        workspace_index_doc_id=workspace_index["id"],
        workspace_index_markdown=workspace_index["markdown"],
        about_doc_id=about["id"],
        privacy_rules_doc_id=privacy_rules["id"],
        privacy_rules=privacy_rules["rules"],
        mcp_usage_guide_doc_id=mcp_usage_guide["id"],
        mcp_usage_guide_markdown=mcp_usage_guide["markdown"],
        workspace_index_guide_doc_id=workspace_index_guide["id"],
        workspace_index_guide_markdown=workspace_index_guide["markdown"],
        workspace_index_updated=workspace_index["updated"],
        workspace_index_is_placeholder=workspace_index["is_placeholder"],
    )


def _ensure_system_notebook(
    client: SiYuanClient,
    language: str,
    detect_existing: bool,
    *,
    cached_notebook_id: str = "",
) -> tuple[str, str, str | None]:
    del detect_existing
    notebooks = client.list_notebooks()

    if cached_notebook_id:
        cached = next(
            (nb for nb in notebooks if str(nb.get("id") or "") == cached_notebook_id),
            None,
        )
        if cached:
            name = str(cached.get("name") or "")
            return cached_notebook_id, name, match_notebook_name(name)

    current_names = [get_notebook_name(language)]
    current_names.extend(
        name for name in all_notebook_names()
        if name not in current_names and match_notebook_name(name)
    )
    for target_name in current_names:
        for notebook in notebooks:
            name = str(notebook.get("name") or "")
            if name.casefold() == target_name.casefold():
                return str(notebook.get("id") or ""), name, match_notebook_name(name)

    target_name = get_notebook_name(language)
    result = client.create_notebook(target_name)
    notebook_id = str(result.get("id") or "")
    if not notebook_id:
        for notebook in client.list_notebooks():
            if str(notebook.get("name") or "") == target_name:
                notebook_id = str(notebook.get("id") or "")
                break
    if not notebook_id:
        raise RuntimeError(f"无法创建系统笔记本：{target_name}")
    return notebook_id, target_name, None


def _list_system_docs(
    client: SiYuanClient, notebook_id: str
) -> list[dict[str, Any]]:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        return client.query_sql(
            "SELECT id, box, path, hpath, markdown, content, updated "
            f"FROM blocks WHERE type='d' AND box='{_sql(notebook_id)}'"
        )


def _doc_title(doc: dict[str, Any]) -> str:
    hpath = str(doc.get("hpath") or "").strip("/")
    return hpath.split("/")[-1] if hpath else ""


def _find_doc_by_key(
    docs: list[dict[str, Any]],
    key: str,
    language: str,
    cached_id: str = "",
) -> dict[str, Any] | None:
    desired_name = get_doc_name(key, language)
    current_names = list(SYSTEM_DOC_NAMES.get(key, {}).values())
    legacy_names = list(LEGACY_DOC_NAMES.get(key, []))

    def find_by_names(names: list[str]) -> dict[str, Any] | None:
        for name in names:
            for doc in docs:
                if _doc_title(doc).casefold() == name.casefold():
                    return doc
        return None

    current = find_by_names([desired_name] + [n for n in current_names if n != desired_name])
    legacy = find_by_names(legacy_names)

    # If both generations exist, the current title is authoritative and the
    # legacy document is deliberately left untouched.
    if current and legacy:
        return current

    if cached_id:
        cached = next(
            (doc for doc in docs if str(doc.get("id") or "") == cached_id),
            None,
        )
        if cached:
            return cached
    return current or legacy


def _cached_doc_id(document_cache: dict[str, Any], key: str) -> str:
    entry = document_cache.get(key)
    return str(entry.get("id") or "") if isinstance(entry, dict) else ""


def _create_document(
    client: SiYuanClient,
    notebook_id: str,
    docs: list[dict[str, Any]],
    name: str,
    markdown: str,
) -> dict[str, Any]:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        result = client.create_doc_with_md(notebook_id, f"/{name}", markdown)
    doc_id = str(result.get("id") or "")
    if not doc_id:
        refreshed = _list_system_docs(client, notebook_id)
        found = next(
            (doc for doc in refreshed if _doc_title(doc).casefold() == name.casefold()),
            None,
        )
        doc_id = str(found.get("id") or "") if found else ""
    if not doc_id:
        raise RuntimeError(f"无法创建系统文档：{name}")
    doc = {
        "id": doc_id,
        "box": notebook_id,
        "hpath": f"/{name}",
        "updated": "",
    }
    docs.append(doc)
    return doc


def _export_markdown(client: SiYuanClient, notebook_id: str, doc_id: str) -> str:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        return client.export_markdown(doc_id)


def _update_document(
    client: SiYuanClient,
    notebook_id: str,
    doc_id: str,
    markdown: str,
) -> str:
    from .indexer import ensure_notebooks_open

    with ensure_notebooks_open(client, [notebook_id]):
        client.update_block(doc_id, markdown)
        return client.export_markdown(doc_id)


def _document_updated(
    client: SiYuanClient, doc: dict[str, Any]
) -> str:
    updated = str(doc.get("updated") or "")
    if updated:
        return updated
    doc_id = str(doc.get("id") or "")
    rows = client.query_sql(
        f"SELECT updated FROM blocks WHERE id='{_sql(doc_id)}' LIMIT 1"
    )
    return str(rows[0].get("updated") or "") if rows else ""


def _record_document(
    document_cache: dict[str, Any],
    key: str,
    doc: dict[str, Any],
    **extra: Any,
) -> None:
    entry = {
        "id": str(doc.get("id") or ""),
        "name": _doc_title(doc),
    }
    entry.update(extra)
    document_cache[key] = entry


def _ensure_ai_preferences(
    client: SiYuanClient,
    notebook_id: str,
    docs: list[dict[str, Any]],
    language: str,
    document_cache: dict[str, Any],
) -> dict[str, Any]:
    key = "ai_guide"
    existing = _find_doc_by_key(
        docs, key, language, _cached_doc_id(document_cache, key)
    )
    template = AI_GUIDE_TEMPLATES.get(language, AI_GUIDE_TEMPLATES["zh-CN"])
    desired_name = get_doc_name(key, language)
    if not existing:
        existing = _create_document(client, notebook_id, docs, desired_name, template)
        markdown = _export_markdown(client, notebook_id, str(existing["id"]))
        _record_document(document_cache, key, existing)
        return {"id": str(existing["id"]), "markdown": markdown}

    doc_id = str(existing.get("id") or "")
    current_title = _doc_title(existing)
    if current_title in LEGACY_DOC_NAMES.get(key, []):
        client.rename_doc_by_id(doc_id, desired_name)
        existing["hpath"] = f"/{desired_name}"

    markdown = _export_markdown(client, notebook_id, doc_id)
    legacy_hashes = {
        markdown_sha256(value) for value in LEGACY_AI_GUIDE_TEMPLATES.values()
    }
    if markdown_sha256(markdown) in legacy_hashes:
        markdown = _update_document(client, notebook_id, doc_id, template)
    _record_document(document_cache, key, existing)
    return {"id": doc_id, "markdown": markdown}


def _ensure_about(
    client: SiYuanClient,
    notebook_id: str,
    docs: list[dict[str, Any]],
    language: str,
    document_cache: dict[str, Any],
) -> dict[str, Any]:
    key = "about"
    template = ABOUT_TEMPLATES.get(language, ABOUT_TEMPLATES["zh-CN"])
    existing = _find_doc_by_key(
        docs, key, language, _cached_doc_id(document_cache, key)
    )
    if not existing:
        existing = _create_document(
            client, notebook_id, docs, get_doc_name(key, language), template
        )
        markdown = _export_markdown(client, notebook_id, str(existing["id"]))
    else:
        desired_name = get_doc_name(key, language)
        if _doc_title(existing) != desired_name:
            client.rename_doc_by_id(str(existing["id"]), desired_name)
            existing["hpath"] = f"/{desired_name}"
        markdown = _export_markdown(client, notebook_id, str(existing["id"]))
        entry = document_cache.get(key)
        entry = entry if isinstance(entry, dict) else {}
        baseline_matches = (
            str(entry.get("id") or "") == str(existing.get("id") or "")
            and str(entry.get("rendered_sha256") or "") == markdown_sha256(markdown)
            and str(entry.get("source_sha256") or "") == source_sha256(template)
        )
        if not baseline_matches and markdown_sha256(markdown) != markdown_sha256(template):
            markdown = _update_document(
                client, notebook_id, str(existing["id"]), template
            )
    _record_document(
        document_cache,
        key,
        existing,
        source_sha256=source_sha256(template),
        rendered_sha256=markdown_sha256(markdown),
        developer_controlled=True,
    )
    return {"id": str(existing["id"]), "markdown": markdown}


def _ensure_privacy_rules(
    client: SiYuanClient,
    notebook_id: str,
    docs: list[dict[str, Any]],
    language: str,
    document_cache: dict[str, Any],
) -> dict[str, Any]:
    key = "privacy_rules"
    existing = _find_doc_by_key(
        docs, key, language, _cached_doc_id(document_cache, key)
    )
    if not existing:
        template = PRIVACY_RULES_TEMPLATES.get(
            language, PRIVACY_RULES_TEMPLATES["zh-CN"]
        )
        existing = _create_document(
            client, notebook_id, docs, get_doc_name(key, language), template
        )
    markdown = _export_markdown(client, notebook_id, str(existing["id"]))
    rules = parse_privacy_rules_markdown(markdown)
    _record_document(document_cache, key, existing)
    return {"id": str(existing["id"]), "markdown": markdown, "rules": rules}


def _ensure_managed_guide(
    client: SiYuanClient,
    root: Path,
    notebook_id: str,
    docs: list[dict[str, Any]],
    language: str,
    document_cache: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    template = load_system_template(root, key, language)
    existing = _find_doc_by_key(
        docs, key, language, _cached_doc_id(document_cache, key)
    )
    if not existing:
        existing = _create_document(
            client,
            notebook_id,
            docs,
            get_doc_name(key, language),
            template.markdown,
        )
        markdown = _export_markdown(client, notebook_id, str(existing["id"]))
        _record_managed_template(document_cache, key, existing, template, markdown)
        return {"id": str(existing["id"]), "markdown": markdown}

    doc_id = str(existing.get("id") or "")
    markdown = _export_markdown(client, notebook_id, doc_id)
    current_hash = markdown_sha256(markdown)
    entry = document_cache.get(key)
    entry = entry if isinstance(entry, dict) else {}
    same_registered_document = str(entry.get("id") or "") == doc_id

    if same_registered_document and bool(entry.get("user_modified")):
        _record_managed_template(
            document_cache, key, existing, template, markdown, user_modified=True,
            baseline_sha256=str(entry.get("rendered_sha256") or ""),
        )
        return {"id": doc_id, "markdown": markdown}

    baseline_hash = str(entry.get("rendered_sha256") or "") if same_registered_document else ""
    if baseline_hash:
        if current_hash != baseline_hash:
            _record_managed_template(
                document_cache, key, existing, template, markdown,
                user_modified=True, baseline_sha256=baseline_hash,
            )
            return {"id": doc_id, "markdown": markdown}
        template_changed = (
            int(entry.get("template_version") or 0) != template.version
            or str(entry.get("source_sha256") or "") != template.source_sha256
        )
        if template_changed:
            markdown = _update_document(client, notebook_id, doc_id, template.markdown)
        _record_managed_template(document_cache, key, existing, template, markdown)
        return {"id": doc_id, "markdown": markdown}

    known_hashes = {
        markdown_sha256(template.markdown),
        *template.historical_normalized_sha256,
    }
    if current_hash in known_hashes:
        if current_hash != markdown_sha256(template.markdown):
            markdown = _update_document(client, notebook_id, doc_id, template.markdown)
        _record_managed_template(document_cache, key, existing, template, markdown)
    else:
        _record_managed_template(
            document_cache, key, existing, template, markdown,
            user_modified=True, baseline_sha256="",
        )
    return {"id": doc_id, "markdown": markdown}


def _record_managed_template(
    document_cache: dict[str, Any],
    key: str,
    doc: dict[str, Any],
    template: SystemTemplate,
    markdown: str,
    *,
    user_modified: bool = False,
    baseline_sha256: str | None = None,
) -> None:
    _record_document(
        document_cache,
        key,
        doc,
        template_version=template.version,
        source_sha256=template.source_sha256,
        rendered_sha256=(
            baseline_sha256
            if baseline_sha256 is not None
            else markdown_sha256(markdown)
        ),
        current_sha256=markdown_sha256(markdown),
        user_modified=user_modified,
    )


def _ensure_workspace_index(
    client: SiYuanClient,
    notebook_id: str,
    docs: list[dict[str, Any]],
    language: str,
    document_cache: dict[str, Any],
) -> dict[str, Any]:
    key = "workspace_index"
    placeholder = WORKSPACE_INDEX_PLACEHOLDERS.get(
        language, WORKSPACE_INDEX_PLACEHOLDERS["zh-CN"]
    )
    existing = _find_doc_by_key(
        docs, key, language, _cached_doc_id(document_cache, key)
    )
    if not existing:
        existing = _create_document(
            client,
            notebook_id,
            docs,
            get_doc_name(key, language),
            placeholder,
        )
    markdown = _export_markdown(client, notebook_id, str(existing["id"]))
    updated = _document_updated(client, existing)
    is_placeholder = markdown_sha256(markdown) == markdown_sha256(placeholder)
    _record_document(
        document_cache,
        key,
        existing,
        placeholder=is_placeholder,
        updated=updated,
    )
    return {
        "id": str(existing["id"]),
        "markdown": markdown,
        "updated": updated,
        "is_placeholder": is_placeholder,
    }


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
        privacy_rules_id = document_ids.get("privacy_rules", "")
        if privacy_rules_id and document_id:
            return document_id == privacy_rules_id
        if system_notebook_id and notebook_id:
            return (
                notebook_id == system_notebook_id
                and match_doc_key(hpath) == "privacy_rules"
            )
        return False
    return match_doc_key(hpath) == "privacy_rules"


def is_system_notebook_name(name: str) -> bool:
    return match_notebook_name(name) is not None


def _sql(value: str) -> str:
    return str(value).replace("'", "''")
