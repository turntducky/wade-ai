---
name: manage_knowledge_base
description: Manage W.A.D.E.'s long-term intelligence by reading/writing workspace documents or updating the structured MEMORY database.
category: memory
risk: medium
parameters:
  action:
    type: string
    enum: [list_files, store_fact, delete_fact, read_full_file, rewrite_full_file, append_to_file]
    description: The operation to perform.
  target:
    type: string
    description: The .md filename (e.g., 'PROJECTS.md', 'USER.md'). Mandatory for all actions except 'list_files'.
  topic:
    type: string
    description: The specific key/category. Required ONLY for 'store_fact' and 'delete_fact'.
  content:
    type: string
    description: The text to write or the fact to store. Required for store, rewrite, and append actions.
required: [action]
---

# manage_knowledge_base

## Persona
You are the Lead Archival Specialist for W.A.D.E. You are responsible for the integrity of the collective intelligence. When storing facts, be concise. When rewriting files, ensure the formatting (headers, lists) remains clean and professional.

## Instructions

### 1. Workspace Discovery
- **Action**: `list_files`
- Use this to see what intelligence is already available.
- Note that files marked `[protected]` (like `BOOTSTRAP.md`) cannot be modified.

### 2. Structured Memory (MEMORY.md)
`MEMORY.md` is a structured database synced from JSON; it cannot be edited like a normal file.
- **Store Fact**: Use `action: 'store_fact'` with a `topic` (key) and `content` (fact).
- **Delete Fact**: Use `action: 'delete_fact'` with the relevant `topic` to purge a specific entry.
- **Constraint**: You cannot use `read_full_file` or `rewrite_full_file` on `MEMORY.md`.

### 3. Document Management (USER.md, PROJECTS.md, etc.)
- **Read**: Use `read_full_file` to ingest the current state of a document before suggesting changes.
- **Write/Overwrite**: Use `rewrite_full_file` for major updates or creating new `.md` files.
- **Append**: Use `append_to_file` to add logs or notes to the end of a document without disturbing existing content.

## Response Handling
The tool returns confirmation of the operation. If an update is redundant (e.g., storing a fact that already exists), the system will notify you that no update was needed. Always verify that your `target` filename ends in `.md` or the system will append it for you.