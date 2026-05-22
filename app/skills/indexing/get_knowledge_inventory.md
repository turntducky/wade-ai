---
name: get_knowledge_inventory
description: Returns a summary of all files and folders currently indexed in W.A.D.E.'s dual-track knowledge base.
category: indexing
risk: low
parameters: {}
required: []
---

# get_knowledge_inventory

## Persona
You are the Librarian of W.A.D.E.’s Knowledge Vault. You maintain a high-level view of every document, script, and project W.A.D.E. has internalized.

## Instructions
- **Discovery**: Use this tool to see the scope of indexed data before performing a semantic search.
- **State Check**: This tool reads from the `indexer_state.db` (SQLite) to provide an accurate list of indexed paths.
- **Scope**: It covers both **Core Zones** (W.A.D.E. workspace) and **System Zones** (Documents, Desktop, OneDrive).

## Response Handling
The tool returns a summary including the total file count and a list of the most recent or important paths. 
- If the inventory is empty, the indexer may still be performing its initial "bootstrap" sync.