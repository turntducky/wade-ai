---
name: patch_host_file
description: Surgically replaces specific line ranges in a host file.
category: workspace
requires_network: false
risk: medium
parameters:
  absolute_path:
    type: string
    description: Full path to the file.
  start_line:
    type: integer
    description: First line to replace (1-indexed).
  end_line:
    type: integer
    description: Last line to replace (inclusive).
  content:
    type: string
    description: The new text to insert.
required: [absolute_path, start_line, end_line, content]
---

# patch_host_file

## Persona
You are a Surgical Editor. You perform precise modifications to minimize risk and context window bloat.

## Instructions
- **Precision**: Verify line numbers using `read_host_file` before patching.
- **Range**: If `start_line` and `end_line` are the same, you are replacing a single line.
- **Efficiency**: Use this for large files where `write_host_file` would be too slow or context-heavy.

## Response Handling
- **Validation**: The tool confirms the old range replaced and the new range occupied. Update your mental map of the file accordingly.