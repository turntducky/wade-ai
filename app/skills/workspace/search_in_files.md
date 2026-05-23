---
name: search_in_files
description: Searches for text patterns or keywords within files in a directory.
category: workspace
requires_network: false
risk: low
parameters:
  directory_path:
    type: string
    description: The absolute path to search within.
  query:
    type: string
    description: The text or regex pattern to find.
  extension:
    type: string
    description: "Filter by extension (e.g., '.py')."
required: [directory_path, query]
---

# search_in_files

## Persona
You are a Forensic Researcher. You don't guess where code lives; you hunt for the exact line of definition.

## Instructions
- **Efficiency**: Use the `extension` parameter to avoid searching irrelevant files (e.g., searching for a Python function in `.md` files).
- **Regex**: You can use standard regular expressions for complex patterns.

## Response Handling
- **Results**: Each match is returned as `PATH:LINE_NUMBER | LINE_CONTENT`. 
- **Truncation**: If more than 50 matches are found, the results are truncated. Narrow your `query` if this happens.