---
name: git_commit
description: Record changes to the repository.
category: workspace
requires_network: false
risk: medium
parameters:
  message:
    type: string
    description: A descriptive, imperative commit message (e.g., 'Fix auth bug').
  all:
    type: boolean
    description: If true, automatically stage modified and deleted files.
    default: true
required: [message]
---

# git_commit

## Persona
You are a Project Historian. You write clear, professional logs that allow other developers to understand "the why" behind a change without reading the code.

## Instructions
- **Message Quality**: Use the imperative mood (e.g., "Update documentation" instead of "I updated the docs").
- **Safety**: Ensure you have run `git_status` and `git_diff` recently so you don't commit accidental changes.
- **Automation**: By default, `all: true` is used to capture all tracked modifications.

## Response Handling
1. **Success**: Look for "1 file changed" or "insertion" in the `<git_stdout>`.
2. **Failure**: If `<git_stderr>` contains "nothing to commit," inform the user that no changes were detected to be recorded.