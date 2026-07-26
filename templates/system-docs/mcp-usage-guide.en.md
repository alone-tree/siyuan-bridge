This guide only supplements principles that tool descriptions do not explain clearly. Refer to the MCP tool descriptions for individual tools and parameters.

## Locating material

- The Workspace Index helps identify likely notebooks and paths, but it is navigation rather than a factual source. Read the original notes after locating them.
- Do not force a fixed list → find → read sequence. Search or read directly when the target is clear; inspect the document tree when the notebook's structure matters.
- The system notebook mainly stores SiYuan Bridge configuration and AI guidance. Do not treat its guides, About document, or Privacy Rules as user knowledge.

## Editing and block references

- Choose an editing action from the final structure the user wants. Do not change the plan merely to preserve block IDs; when no backlinks exist, whether an old ID survives normally has no practical effect.
- Operations that remove block IDs automatically check backlinks. When backlinks exist, the tool refuses the operation and explains when the referenced ID should be preserved and when the user may be asked to allow the reference to break.
- Re-plan the edit from that conflict guidance. Use `reference_policy=break` only after the user explicitly accepts breaking the references reported for that operation; it is not standing permission for later changes.

## System notebook

- **MCP Usage Guide:** additional MCP usage principles; user-editable.
- **User Preferences:** the user's long-term instructions for AI; follow them and do not proactively rewrite them.
- **Workspace Index:** navigation for new sessions, created or updated by AI when requested.
- **Workspace Index Guide:** rules for creating and updating the index; user-editable.
- **About SiYuan Bridge:** user-facing product information that upgrades may overwrite.
- **Privacy Rules:** maintained by the user and inaccessible to AI.

Except for Privacy Rules, documents in the system notebook behave like ordinary visible documents.
