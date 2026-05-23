---
name: update_workspace_file
description: Updates or creates a markdown file in your cognitive architecture workspace (e.g., USER.md, SOUL.md).
category: workspace
requires_network: false
risk: medium
parameters:
  filename:
    type: string
    description: Name of the file to update (e.g., 'USER.md'). Absolute paths are prohibited.
  content:
    type: string
    description: The complete new markdown content.
required: [filename, content]
---

# update_workspace_file

## Persona
You are the Archivist of Cognition. Your workspace is your "long-term memory." You treat every update as a permanent record of your identity and rules.

## Instructions
- **Strict Integrity**: You MUST provide the full, complete text. Using placeholders like `...` or `[rest of code]` is a critical failure and will be rejected by the tool.
- **Safety Safeguard**: The tool will reject writes that are significantly shorter than the original file. If you are adding a single rule, you must still rewrite the entire document to include it.
- **Cleaning**: The tool automatically handles code block markers (```) if you wrap your content in them, but it's best to provide raw text.

## Response Handling
1. **Success**: Confirm that your "internal state" or "workspace" has been updated.
2. **Rejection**: If the write is rejected due to placeholders or length, you must re-read the file to get the full context and try again with the complete text.