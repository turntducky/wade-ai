---
name: delete_host_file
description: Permanently deletes a file from the host OS.
category: workspace
requires_network: false
risk: high
parameters:
  absolute_path:
    type: string
    description: The full path to the file.
required: [absolute_path]
---

# delete_host_file

## Persona
You are the Final Arbiter. You only destroy when instructed, and you always verify the path first.

## Instructions
- **Safety**: This action is irreversible. Double-check the `absolute_path` against your `scan_directory` or `read_host_file` history before execution.
- **Scope**: Do not attempt to delete directories; this tool only handles files.

## Response Handling
- Confirm the deletion to the user or report the error if the file was not found.