---
name: delete_workspace_file
description: Deletes a file from your cognitive architecture workspace.
category: workspace
reversible: false
risk: high
parameters:
  filename:
    type: string
    description: Name of the file to delete (e.g., 'BOOTSTRAP.md')
required: [filename]
---

# delete_workspace_file

## Instructions
- Use this to remove outdated or temporary files from the workspace.
- This action is permanent within the workspace folder.
