# MCP Usage Guide

This document explains how AI should combine SiYuan Bridge tools. Refer to each MCP tool description for its exact parameters.

## Recommended workflow

1. Start each new session with `siyuan_start`, then read the user preferences, notebook overview, and Workspace Index in the startup packet.
2. Use `siyuan_list` to browse one document-tree level and `siyuan_find` to search content.
3. Use `siyuan_read` to read documents; continue long documents with `block_start`.
4. Before editing, read the target again with `include_block_ids=true`.
5. Pass `confirmed=true` only when the user has explicitly requested a write.

## Editing and block IDs

- When replacing one block with one block, prefer `single_block_replace` so the original block ID is preserved.
- When the structure must change, let the AI choose `multi_block_replace` from the user's intent. Do not mechanically match old and new blocks by position.
- `delete`, `multi_block_replace`, document overwrite, and document-tree deletion check whether disappearing block IDs have external references.
- References reject the operation by default. Report visible sources and the count of hidden sources first. Retry the same operation with `reference_policy=break` only after the user explicitly allows those references to break.
- Do not automatically rewrite references in other documents.

## Paths, permissions, and refresh

- Prefer the complete document path returned by tools. If a path is stale, call `siyuan_operate(action="refresh")` or use `document_id`.
- `read_only` content can be read, copied, and exported, but not edited, renamed, moved, or deleted.
- The Privacy Rules document is maintained by the user in SiYuan. AI must not read or modify it.
- `siyuan_start` clears the temporary AI workspace; refreshing the index during a session does not.

## Workspace Index

The Workspace Index helps new sessions locate notes quickly. See the system notebook's Workspace Index Guide for creation and update instructions.
