---
name: scan_directory
description: Generates a visual tree structure of a directory to understand project architecture.
category: workspace
requires_network: false
risk: low
parameters:
  directory_path:
    type: string
    description: The absolute path of the directory to scan.
  max_depth:
    type: integer
    description: Recursion limit for the scan.
    default: 2
required: [directory_path]
---

# scan_directory

## Persona
You are a Cartographer of Systems. You map out environments before you attempt to navigate or modify them.

## Instructions
- **Orientation**: Always run this before `read_host_file` or `search_in_files` if you are unfamiliar with a project's layout.
- **Noise Filtering**: This tool automatically ignores heavy folders like `.git`, `node_modules`, and `__pycache__`.

## Response Handling
- **Tree Interpretation**: Look for the 📁 (Folder) and 📄 (File) icons to distinguish between directories and assets. 
- **Depth**: If the output shows `[...] max depth reached`, and you need to see deeper, call the tool again on a specific sub-directory.