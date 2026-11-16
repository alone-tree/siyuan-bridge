This guide only supplements principles that tool descriptions do not explain clearly. Refer to the MCP tool descriptions for individual tools and parameters.

## Locating material

- The Workspace Index helps identify likely notebooks and paths, but it is navigation rather than a factual source. Read the original notes after locating them.
- Do not force a fixed list → find → read sequence. Search or read directly when the target is clear; inspect the document tree when the notebook's structure matters.
- The system notebook mainly stores SiYuan Bridge configuration and AI guidance. Do not treat its guides, About document, or Privacy Rules as user knowledge.

## Editing and block references

- Choose an editing action from the final structure the user wants. Do not change the plan merely to preserve block IDs; when no backlinks exist, whether an old ID survives normally has no practical effect.
- Operations that remove block IDs automatically check backlinks. When backlinks exist, the tool refuses the operation and explains when the referenced ID should be preserved and when the user may be asked to allow the reference to break.
- Re-plan the edit from that conflict guidance. Use `reference_policy=break` only after the user explicitly accepts breaking the references reported for that operation; it is not standing permission for later changes.

## Attachments and sync

- Local files and images are uploaded as content. Directories use native local links without recursion. Files of 256MiB or more prompt first; pass `upload_large_files=true` only after user consent. This applies to `insert_assets` and create/edit with `markdown_file`.
- Check per-item `success/partial/failed` results. Failed attachments do not undo successful items. Do not recreate a document whose Markdown body was already written. Block insertion/update failures may leave resources; do not automatically delete them or compensate the whole batch. The user can restore a snapshot manually.
- Ask for a sync direction only when the native prompt requires it, then pass that choice as `upload=true/false`, with no default direction. Omit it for ordinary sync; the native empty-cloud exception uploads automatically. Do not guess or fill internal preview state. New connections clear prior choices. A timeout or connection failure may occur after sync started; do not retry automatically or infer completion from HTTP success.

## System notebook

The system notebook maintains exactly four documents:

- **User Preferences:** the user's long-term instructions for AI; follow them and do not proactively rewrite them.
- **Workspace Index:** navigation for new sessions, created or updated by AI when requested.
- **About SiYuan Bridge:** user-facing product information that upgrades may overwrite.
- **Privacy Rules:** maintained by the user and inaccessible to AI.

Except for Privacy Rules, documents in the system notebook behave like ordinary visible documents.

Before creating or updating the Workspace Index, call `siyuan_operate(action="read_guide", guide="workspace_index")` to read the complete guide. To read this usage guide again, call `siyuan_operate(action="read_guide", guide="mcp_usage")`. Both guides ship with the plugin version; neither is user-maintained content.
