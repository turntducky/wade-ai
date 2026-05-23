---
name: append_workspace_file
description: Quickly adds new text to the bottom of an existing workspace markdown file.
category: workspace
requires_network: false
risk: medium
parameters:
  filename:
    type: string
    description: Name of the file to append to (e.g., 'TOOLS.md').
  new_text:
    type: string
    description: The new text/note to add to the bottom.
required: [filename, new_text]
---

# append_workspace_file

## Persona
You are the Incremental Logbook. You use this tool for "fast-writes" where you don't need to reorganize existing knowledge, just extend it.

## Instructions
- **Use Case**: Ideal for adding new "Learned Rules," "Project Ideas," or "Session Logs."
- **Pre-requisite**: The file must already exist. If it doesn't, use `update_workspace_file` first.

## Response Handling
- Verify the success message. If the file is missing, fallback to creating it via `update_workspace_file`.