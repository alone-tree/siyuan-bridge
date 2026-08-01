from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SYSTEM_STATE_SCHEMA_VERSION = 2
SYSTEM_STATE_PATH = Path("knowledge_base") / "system_state.json"


def load_system_state(root: Path) -> dict[str, Any]:
    path = root / SYSTEM_STATE_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    workspaces = data.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = {}
    return {
        "schema_version": SYSTEM_STATE_SCHEMA_VERSION,
        "active_workspace_key": str(data.get("active_workspace_key") or ""),
        "workspaces": workspaces,
    }


def document_entries(value: Any) -> list[dict[str, Any]]:
    """Return document registry entries from either schema v1 or v2."""
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict) and entry.get("id")]
    if isinstance(value, dict) and value.get("id"):
        return [value]
    return []


def active_system_ids(root: Path) -> tuple[str, dict[str, set[str]]]:
    state = load_system_state(root)
    key = str(state.get("active_workspace_key") or "")
    workspace = state.get("workspaces", {}).get(key, {})
    if not isinstance(workspace, dict):
        return "", {}
    notebook = workspace.get("system_notebook", {})
    notebook_id = str(notebook.get("id") or "") if isinstance(notebook, dict) else ""
    documents = workspace.get("documents", {})
    if not isinstance(documents, dict):
        return notebook_id, {}
    ids = {
        str(doc_key): {
            str(entry.get("id") or "")
            for entry in document_entries(value)
            if entry.get("id")
        }
        for doc_key, value in documents.items()
    }
    return notebook_id, ids
