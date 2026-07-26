# Privacy Rules

This document controls which notes are hidden from AI. AI cannot read this document.

Only edit the two tables below. You may add or remove rows, but do not add tables or edit table headers.

**Permission model**: Use the `Permission` column to control access.

- `read_write` (default): AI can read and write (writing still requires confirmed=true). Leave blank for default.
- `read_only`: AI can read, list, search, copy/export, but cannot create/edit/rename/move/delete.
- `hidden`: AI cannot see, search, or access the content at all.

Only add rules for notebooks or documents that need restrictions; unlisted defaults to read_write.
For notebooks, Notebook ID is preferred. To get it, click the three-dot menu next to the notebook, open Settings, and click Copy ID. If you do not know the ID yet, use Notebook Name. If multiple notebooks share the same name, all matching notebooks will be affected.

Document hiding requires Document ID. Title is only for your confirmation and is not used for matching.

## Notebook Permissions

| Permission | Notebook ID | Notebook Name | Reason |
|---------------|-------------------------|---------------|-----------------|
| hidden | 20260503123456-abcdefg | Example: Private Data | Fully hidden |
| read_only | 20260503123456-abcdefg | Example: Reference | Read-only |

## Document Permissions

| Permission | Document ID | Title | Reason |
|---------------|------------------------|-------------------------------|-----------------|
| hidden | 20260503123456-abcdefg | Example: Unpublished Project | Fully hidden |
| read_only | 20260503123456-abcdefg | Example: Important Reference | Read-only |
