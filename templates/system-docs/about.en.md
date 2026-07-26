<!-- template_version: 5 -->

# About SiYuan Bridge

This document is maintained and overwritten by SiYuan Bridge. Do not store important content here.

SiYuan Bridge is a local bridge between SiYuan notes and AI agents, letting AI read, search, and maintain your knowledge base under privacy rules.

## Current Tool Capabilities

- `siyuan_start`: startup entry, refreshes the safe index and returns the MCP Usage Guide, User Preferences, notebook overview, and Workspace Index.
- `siyuan_list`: lists visible notebooks and document trees.
- `siyuan_find`: searches the visible knowledge base.
- `siyuan_read`: reads documents by block windows; reference reading exposes block indexes and IDs for editing.
- `siyuan_create`: creates new documents.
- `siyuan_edit`: structured editing based on reference-reading coordinates, including replace, insert, append, delete, and normal Markdown table edits.

## Six Documents in This Notebook

- **MCP Usage Guide**: Tool combinations and important pitfalls. You may edit it or reset it in plugin settings.
- **Workspace Index Guide**: Instructions for creating and updating the Workspace Index. You may edit it or reset it in plugin settings.
- **User Preferences**: Your long-term instructions for AI. The system only ensures that it exists and never overwrites its body.
- **Workspace Index**: AI-generated semantic navigation map for new sessions.
- **Privacy Rules**: Human-maintained Markdown tables controlling which notes are hidden or read-only from AI. AI cannot read this document.
- **About SiYuan Bridge**: This document — a human-readable introduction to the tool.

## How to Use

Write notes in SiYuan as usual. When needed, ask AI to search your notes. When asking AI to edit, let it read the target document first and confirm the target position; a SiYuan workspace snapshot is created before writing. To hide or restrict content from AI, add rules in the Privacy Rules document tables. The system notebook is otherwise handled like a normal notebook; only the Privacy Rules document is hard-isolated from AI.

For more details, read the project README, visit the project website, or contact the developer.
