The Workspace Index is semantic navigation for helping a new session locate material quickly. It should answer which notebook and path contain relevant material and what to read first. It is not a copy of the full directory, a replacement for search, or a summary of every document.

## Reading scope

Inspect the size and full hierarchy of every visible notebook before deciding how it is organized:

- summarize deep trees by major paths and structural patterns;
- list principal documents in small, flat notebooks;
- treat index pages, project introductions, and overview documents as hubs.

## Creation modes

- **Fast mode (default):** obtain each notebook's complete document tree down to the deepest level, then read a small number of representative documents in every notebook before summarizing it. Use creation time, word count, block count, position, and other available signals together when selecting documents.
- **Detailed mode (only when explicitly requested):** expand the reading scope beyond fast mode and learn as much of the important detail as practical.

## Index contents

Include:

1. generation or update date;
2. a small number of reliable “question area → notebook” routes;
3. each notebook's structural pattern, principal contents, and important documents;
4. a place for user notes or priorities.

Prefer missing routes over wrong ones. Several small notebooks may be combined into one short description or list. Group large notebooks by path and identify hubs, representative documents, and principal contents. Keep the entire index under 1,000 words according to SiYuan's word count. Describe the system notebook only as configuration; do not treat its guides, About document, or Privacy Rules as user knowledge.

Suggested structure:

```markdown
> Updated: YYYY-MM-DD

## Quick navigation
| Question area | Notebook or path |
|---|---|

## Notebook name
> Structure: …
> User notes:

### /major/path
- Hub document: one-sentence summary
```

## Update rules

Write only when the user asks to create or update the index, or agrees after the startup packet reports that it is stale. Compare current paths, document counts, and update times, then revisit only added, removed, or potentially changed areas.

Preserve user-written priorities, corrections, explanations, and other human content. When authorship is uncertain, keep the text. Report only what was added, removed, or re-summarized.
