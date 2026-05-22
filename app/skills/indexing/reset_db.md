---
name: reset_database
description: Permanently delete W.A.D.E.'s vector memory (ChromaDB) and indexer state (SQLite).
category: indexing
reversible: false
risk: high
parameters: {}
required: []
---

# reset_database

## Persona
You are the System Safeguard. This is a "Nuclear Option." Use it only when the system state is corrupted beyond repair or the user explicitly demands a total memory wipe.

## Instructions
- **Destructive Action**: This tool wipes the `vector_store` directory and the `indexer_state.db` file. This is permanent.
- **Lock Verification**: The tool will fail if the W.A.D.E. Gateway is active (checked via `gateway.pid` or Port 8000) because ChromaDB locks its files during operation.
- **User Confirmation**: This tool requires a manual `(y/n)` confirmation in the terminal; you should warn the user of this before triggering it.

## Response Handling
- **Success**: The system will report a "deep clean" and state that the brain will rebuild upon the next startup.
- **Failure**: If a "Permission Error" occurs, it means a background process is still holding a lock on the database files.