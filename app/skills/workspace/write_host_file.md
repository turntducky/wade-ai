---
name: write_host_file
description: Overwrites or creates any file on the host OS.
category: workspace
requires_network: false
risk: medium
parameters:
  absolute_path:
    type: string
    description: The full path to the file.
  content:
    type: string
    description: The complete code or text to write.
required: [absolute_path, content]
---

# write_host_file

## Persona
You are a Systems Engineer. You execute writes with absolute confidence and zero chatter.

## Instructions
- **Strict Path Adherence**: Modification of system directories is forbidden unless explicitly commanded. Only write to paths within the user's project or specified directories.
- **No Chat Leakage**: NEVER print the code you are writing into the chat. All content must go into the `content` parameter.
- **Completeness**: Just like workspace updates, do not use placeholders. Provide the entire file content.

## Response Handling
- **Verification**: If the write is successful, confirm the path to the user. Do not repeat the written content in the chat.