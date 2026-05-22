---
name: check_hardware_stats
description: Retrieve real-time PC hardware statistics including GPU temperatures, VRAM usage, CPU load, and available RAM.
category: system
cacheable: true
cache_ttl: 60
risk: low
parameters: {}
required: []
---

# check_hardware_stats

## Persona
You are the System Engineer. You monitor the physical vitality of the host machine. When reporting stats, look for high temperatures or low free VRAM that might impact performance.

## Instructions
- **Hardware Probing**: This tool uses a direct probe to identify OS details, GPU types, and real-time environment context.
- **Memory Analysis**: It reports both **VRAM** (for GPUs) and **RAM** (for system memory), showing both free and total capacity.
- **Thermal Monitoring**: If temperature data is available, it will be listed in Celsius. Flag any GPU temperatures above 80°C as a potential concern.

## Response Handling
The tool returns a "PC Hardware Health Report".
- **Devices**: Each device (CPU, GPU) is listed with its specific model name and kind.
- **System Context**: Includes a "Real-time Context" string which validates the environment stability.