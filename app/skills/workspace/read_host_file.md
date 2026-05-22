---
name: read_host_file
description: Reads any file on the host OS with memory-safe pagination.
category: workspace
requires_network: false
risk: low
parameters:
  absolute_path:
    type: string
    description: The full path to the file.
  start_line:
    type: integer
    description: Line number to start reading (1-indexed).
    default: 1
  end_line:
    type: integer
    description: Line number to stop reading.
required: [absolute_path]
---

# read_host_file

## Persona
You are a High-Fidelity Reader. You ingest technical data with precision, paying close attention to line numbers for future patching.

## Instructions
- **Pagination**: For large files, the output will be truncated. Use the `start_line` parameter to "scroll" through the file.
- **Safety**: The header confirms the file size. If a file is massive (several MBs), be conservative with your `end_line` range.

## Response Handling
- **Line Markers**: The output includes line numbers (e.g., `  42 | code()`). Use these exact numbers when calling `patch_host_file`.
- **Metadata**: Note the file size in the header to estimate how much more content remains.