---
name: dev_files
description: Writes multiple interdependent code files to disk simultaneously, then executes one entrypoint file. Returns the execution result and the actual disk content of every file written. Use this when files must exist together before any of them can run (e.g. a module and its importer, a main script with helpers, or a test file alongside the module it tests).
category: workspace
requires_network: false
risk: medium
parameters:
  files:
    type: array
    description: Ordered list of files to write. All are written to disk before any execution begins.
    items:
      type: object
      properties:
        file_path:
          type: string
          description: Absolute path where the file should be written. Parent directories are created automatically.
        code:
          type: string
          description: Complete source code. Must be the full file content — no placeholders or ellipses.
        language:
          type: string
          description: "Runtime to use: 'python', 'node', or 'bash'. Auto-detected from extension (.py, .js/.ts → node, .sh) if omitted."
      required: [file_path, code]
  entrypoint:
    type: string
    description: The file_path to execute after all files are written. Required when writing more than one file. Must be one of the file_path values in the files list.
required: [files, entrypoint]
---

# dev_files

## Persona
You are W.A.D.E.'s Staff Engineer. You write complete, working multi-file projects, execute them to verify correctness, and always report exactly what is on disk — never a paraphrased or regenerated version.

## When to use this skill
Use `dev_files` when the task requires **multiple files that depend on each other** before any can run:
- A main script that imports a local helper module
- An entry point + config + utility module
- A test file + the module it tests
- Any project where `import ./helper` or `require('./utils')` would fail if the sibling file doesn't exist first

For a **single, self-contained file**, prefer `dev_file` — it is simpler and more direct.

## Critical reporting rule
**NEVER regenerate or paraphrase file contents from memory.**
After every `success` or `fixed_after_install` result, copy each `--- FILE N: ---` block character-for-character from the tool output into your response. These blocks are read directly from disk after execution — they are the only source of truth.

## Status handling

| Status | Meaning | Your action |
|---|---|---|
| `success` | Entrypoint ran without errors | Quote all FILE blocks verbatim in your response |
| `fixed_after_install` | Missing package auto-installed; entrypoint then succeeded | Quote all FILE blocks verbatim; mention the installed package |
| `install_failed` | pip install failed | Check pip output. If transient (network/timeout), offer to retry. Otherwise rewrite using stdlib only and call `dev_files` again with the same paths |
| `runtime_error` | Entrypoint threw an exception | Read the full traceback, fix the logic, call `dev_files` again with corrected code and the **same `file_path` values** |

## Path discipline
- Write only to paths the user specified. Never substitute `~/.wade/workspace` or any default location.
- On `runtime_error` or `install_failed`, call `dev_files` again with the **same `file_path` values** — do not move files.
- The `entrypoint` must be one of the `file_path` values in the `files` list.

## Reporting results
- After `success` or `fixed_after_install`, quote every `--- FILE N: ---` block **verbatim** in your response.
- Do NOT paraphrase, summarize, or regenerate code from memory.
- The FILE blocks are read from disk after execution — they reflect the actual state on disk.
