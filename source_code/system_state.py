from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SYSTEM_STATE_SCHEMA_VERSION = 1
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


def save_system_state(root: Path, state: dict[str, Any]) -> None:
    path = root / SYSTEM_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SYSTEM_STATE_SCHEMA_VERSION,
        "active_workspace_key": str(state.get("active_workspace_key") or ""),
        "workspaces": state.get("workspaces") if isinstance(state.get("workspaces"), dict) else {},
    }
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp_path.replace(path)


def get_workspace_state(state: dict[str, Any], key: str) -> dict[str, Any]:
    workspaces = state.setdefault("workspaces", {})
    workspace = workspaces.get(key)
    if not isinstance(workspace, dict):
        workspace = {}
        workspaces[key] = workspace
    documents = workspace.get("documents")
    if not isinstance(documents, dict):
        workspace["documents"] = {}
    return workspace


def update_workspace_metadata(
    state: dict[str, Any],
    key: str,
    *,
    notebook_id: str,
    notebook_name: str,
) -> dict[str, Any]:
    workspace = get_workspace_state(state, key)
    workspace["system_notebook"] = {
        "id": notebook_id,
        "name": notebook_name,
    }
    workspace["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    state["active_workspace_key"] = key
    return workspace


def active_system_ids(root: Path) -> tuple[str, dict[str, str]]:
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
        str(doc_key): str(entry.get("id") or "")
        for doc_key, entry in documents.items()
        if isinstance(entry, dict) and entry.get("id")
    }
    return notebook_id, ids
