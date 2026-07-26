# Workspace Index Guide

The Workspace Index is a path-first semantic navigation document stored in the system notebook. AI creates or updates it only when requested by the user; the system never scans the workspace and rewrites it automatically.

## Reading depth

- **Quick (default):** Read one hub document per notebook, such as an index, overview, project background, required reading, or README document.
- **Detailed:** In addition to hub documents, read 2–4 important documents in each key notebook.

Never write a content summary from the title alone. For documents you have not read, record only their path.

## Creation workflow

1. Call `siyuan_start` for the notebook overview and statistics.
2. Call `siyuan_list` for each non-empty notebook and classify its structure as a deep tree, flat collection, or hub-and-spoke layout.
3. Read hub documents with `siyuan_read` at the depth requested by the user.
4. Generate the index with the template below.
5. A user's request to create the index is itself write confirmation. Use `siyuan_create` to overwrite the placeholder in Workspace Index.

```markdown
# Knowledge Base Index
> Generated YYYY-MM-DD

## Quick Navigation
| Topic | Notebook |
|------|----------|
| ... | ... |

## Notebook Name (N documents)

> Structure: one-sentence organization summary
> Priority:

### /path/to/key/subtree

- `doc-id` Document title
  - AI summary: summarize only content actually read
```

## Content rules

- Keep Quick Navigation conservative; a wrong route is worse than a missing route.
- Leave `> Priority:` empty for the user and preserve it exactly during updates.
- Preserve user corrections, notes, and all other manually written content.
- List every document for notebooks with no more than 20 documents; summarize larger notebooks by path and structure pattern.
- Keep the complete index within roughly 300 lines.
- System guides, About, and Privacy Rules are not user knowledge and must not be summarized as such.

## Update workflow

1. Call `siyuan_operate(action="refresh")` to refresh the objective index.
2. Call `siyuan_start` and compare the current overview with the existing Workspace Index.
3. Recheck only new, removed, or potentially changed notebooks and documents.
4. Read new or changed key documents; never infer summaries from titles.
5. Use reference reading to get block IDs, then update Workspace Index locally with `siyuan_edit`.
6. Preserve user priorities, corrections, and all other manually written content.

After updating, report which index entries were added, removed, or refreshed.
