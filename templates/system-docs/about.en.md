<!-- template_version: 6 -->

This document is maintained by SiYuan Bridge. When the plugin is activated or its template is upgraded, the system restores the standard title by name and overwrites the body. Do not store important content here.

SiYuan Bridge is a local bridge between SiYuan notes and AI agents. It lets AI read, search, and maintain your knowledge base under privacy rules.

## Everyday use

You can ask AI to find, read, or organize material in SiYuan. Before modifying notes, AI locates the target content; a SiYuan workspace snapshot is created before writing. If deleting a block would break references from other notes, backlink protection refuses the operation by default.

## System notebook

When activated, the plugin automatically creates and maintains the following documents by name:

- **User Preferences:** your long-term instructions for AI. The system ensures that it exists but does not overwrite its body.
- **Workspace Index:** AI-created semantic navigation for helping new sessions locate material.
- **Privacy Rules:** controls which notes are hidden or read-only. This is the only document hard-isolated from AI.
- **About SiYuan Bridge:** this document. Its title and body are maintained by the plugin and may be overwritten during upgrades.

Except for Privacy Rules, the system notebook can be read and maintained by AI like an ordinary notebook.

> Since 1.11.2, the MCP Usage Guide and the Workspace Index Guide are no longer system notebook documents: they ship with the plugin version. The MCP Usage Guide appears in the `siyuan_start` startup packet, and the startup packet gives the path of the index guide for AI to read on demand. Same-name documents created by earlier versions are no longer used and can be deleted.

For more details, read the project README, visit the project website, or contact the developer.
