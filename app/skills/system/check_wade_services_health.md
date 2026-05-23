---
name: check_wade_services_health
description: Diagnose W.A.D.E.'s internal background services, active models, and administrative privileges.
category: system
cacheable: true
cache_ttl: 30
risk: low
parameters: {}
required: []
---

# check_wade_services_health

## Instructions
- **Service Monitoring**: Checks the status of the Gateway (via PID file), the WhatsApp Bridge (via Node.js process search), and Browser services.
- **Network Ports**: Verifies that the Browser Service is listening on ports `9222` (Headed) and `9223` (Headless).
- **God Mode**: Identifies if W.A.D.E. is running with elevated **Administrative Privileges** (God Mode), which is required for certain system-level tasks.
- **Active Models**: This tool also includes a summary of the currently active AI model mappings for different roles (Chat, Tools, etc.).

## Response Handling
- **Status Indicators**: Uses `🟢 ONLINE`, `🟡 INACTIVE`, and `🔴 OFFLINE` to represent service health.
- **Troubleshooting**: If services are offline, recommend using `perform_system_recovery` to attempt a self-heal.