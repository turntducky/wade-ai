---
name: perform_system_recovery
description: Execute safe, predefined recovery actions to heal the system.
category: system
risk: high
parameters:
  action:
    type: string
    enum: [restart_whatsapp_bridge, clear_stale_pid, restart_gateway, provision_browser_service]
    description: The recovery protocol to execute.
required: [action]
---

# perform_system_recovery

## Instructions
- **Verify First**: Always run `check_wade_services_health` before attempting a recovery.
- **Protocols**:
    - `restart_whatsapp_bridge`: Kills all existing Node.js bridge processes and spawns a fresh instance.
    - `clear_stale_pid`: Removes the `.wade/gateway.pid` file. Use this if the system thinks it's already running but isn't.
    - `provision_browser_service`: Installs the Playwright Chromium binaries required for browser-based skills.
- **Gateway Limitation**: You **cannot** restart the main Gateway (`restart_gateway`) from within the tool; you must ask the user to do this via the CLI.

## Response Handling
- **Success**: Confirms the action taken (e.g., "✅ Local browser binaries provisioned").