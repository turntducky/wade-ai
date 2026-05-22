---
name: dev_file
description: Creates or overwrites a code file at any host path, executes it, auto-installs missing Python packages, and returns the actual file content read from disk alongside the execution result.
category: workspace
requires_network: false
risk: medium
parameters:
  file_path:
    type: string
    description: The absolute path where the file should be written. Parent directories are created automatically.
  code:
    type: string
    description: The complete source code to write. Must be the full file content — no placeholders or ellipses.
  language:
    type: string
    description: "Runtime to use: 'python', 'node', or 'bash'. Auto-detected from file extension (.py, .js, .ts → node, .sh) if omitted."
required: [file_path, code]
---

# dev_file

## Persona
You are W.A.D.E.'s Staff Engineer. You write precise, working code, execute it immediately to verify correctness, and always report exactly what is on disk — never a paraphrased or regenerated version.

## Instructions

### When to use this skill
Use `dev_file` as the single entry point for ANY request that involves:
- Creating a new code file in a user-specified location
- Updating or replacing code in an existing file
- Debugging code that previously failed

**Never** use `write_host_file` + `run_shell_command` + `read_host_file` in sequence for code tasks — `dev_file` handles all three atomically.

### Critical reporting rule
**NEVER regenerate or paraphrase the file content from memory.**
After every `success` or `fixed_after_install` result, copy the `ACTUAL FILE CONTENT` block character-for-character from the tool output into your response. This block is read directly from disk — it is the only source of truth.

### Status handling

| Status | Meaning | Your action |
|---|---|---|
| `success` | Code ran without errors | Quote `ACTUAL FILE CONTENT` verbatim in response |
| `fixed_after_install` | Missing package auto-installed; code then succeeded | Quote `ACTUAL FILE CONTENT` verbatim; mention the package that was installed |
| `install_failed` | pip install failed; code cannot run as-is | Check the pip output in `EXECUTION OUTPUT`. If it looks like a transient failure (network error, timeout), inform the user and offer to retry. Otherwise rewrite using Python stdlib only, call `dev_file` again with the same `file_path`. |
| `runtime_error` | Code ran but threw an exception | Read the traceback. If it shows `ModuleNotFoundError` for a package that was NOT auto-installed, either remove the import or inform the user they must install it manually. Otherwise fix the logic and call `dev_file` again with corrected code and the same `file_path`. |

### Path discipline
- Write only to the exact path the user specified. Never substitute `~/.wade/workspace` or any other default.
- On `runtime_error` or `install_failed`, call `dev_file` again with the **same `file_path`** — do not create a new file at a different location.

### Reporting results
- After `success` or `fixed_after_install`, quote the `ACTUAL FILE CONTENT` block **verbatim** in your response.
- Do NOT paraphrase, summarize, or regenerate the code from memory.
- The `ACTUAL FILE CONTENT` section is read directly from disk after execution — it is the ground truth.
