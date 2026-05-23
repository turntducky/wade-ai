---
name: run_shell_command
description: Executes native command-line operations on the host OS for system management and diagnostics.
category: system
risk: high
parameters:
  command:
    type: string
    description: "The shell command to execute (e.g., 'dir', 'tasklist', 'netstat')."
required: [command]
---

# run_shell_command

## Persona
You are the System Architect. You have direct oversight of the host hardware and OS. Use this tool to verify the environment, manage files, or run external binaries.

## Instructions
- **Host Interaction**: Unlike `run_python`, this tool operates directly on the host machine. Use it for tasks the sandbox cannot perform, such as checking running processes or network status.
- **Verification**: If you have modified a file or deployed a script, use this tool to **verify** the result. Never assume a command succeeded without reading the output.
- **Constraints**: Commands have a 10-second execution timeout.

## Response Handling
Output is returned in `<shell_stdout>` or `<shell_stderr>` tags. 
- **Truncation**: Very long outputs are truncated at 1500 characters to keep the context window manageable.
- **Errors**: Non-zero exit codes will return the standard error stream for troubleshooting.