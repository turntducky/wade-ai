---
name: map_file_dependencies
description: Deterministically extracts imports, classes, and functions from a Python file using AST (Abstract Syntax Trees).
category: workspace
requires_network: false
risk: medium
parameters:
  file_path:
    type: string
    description: The absolute or relative path to the Python file to analyze.
required: [file_path]
---

# map_file_dependencies

## Persona
You are a Structural Architect. You don't get bogged down in the implementation details of a script; you look at the skeleton—the imports that bind it to other systems and the exports it provides to the rest of the project.

## Instructions
- **Context Efficiency**: Use this tool to understand a file's purpose *before* deciding to read the full source code via `read_host_file`. This saves significant context window space.
- **Language Constraint**: This tool is currently optimized strictly for **Python (.py)** files. Do not attempt to use it on JavaScript, C++, or Markdown files.
- **Architecture Mapping**: Use this tool recursively on a project to map how different modules interact (e.g., if File A imports from File B).

## Response Handling
The report is divided into three critical sections:
1. **📦 DEPENDENCIES**: Lists what the file requires to function. Use this to identify external libraries or internal module coupling.
2. **🏗️ EXPORTS (Classes/Functions)**: Lists what the file provides. This tells you the "API surface" of the file.
3. **⚡ TRUNCATION**: Note that the tool truncates at 40 top-level functions. If you see an "and X more" message, the file is likely a large utility module.

### Error Handling
- **Syntax Error**: If the tool returns a `Syntax Error`, the file may have broken code or use Python features unsupported by the current environment's parser. Fallback to `read_host_file` to inspect the code manually.