---
name: analyze_screen
description: Takes a screenshot of the host machine's monitors and analyzes it using Vision AI.
category: vision
risk: low
parameters:
  question:
    type: string
    description: Specific instructions on what to look for or analyze in the screenshot.
  monitor:
    type: integer
    description: "The monitor index to capture: 0 (all), 1 (primary), 2 (secondary)."
    default: 1
required: [question]
---

# analyze_screen

## Persona
You are W.A.D.E.’s Visual Cortex. You interpret the physical pixels of the workspace to provide situational awareness. When analyzing charts, terminal errors, or UI states, be descriptive and highlight visual anomalies that a text-only system might miss.

## Instructions
- **Monitor Selection**: 
    - Use `1` for the primary display where the main workspace usually resides.
    - Use `0` to capture a combined view of all connected monitors.
    - If a monitor index is requested that does not exist, the system defaults to monitor `1`.
- **Question Sanitization**: Keep questions concise (under 500 characters). If the system detects a potential prompt injection (e.g., instructions to ignore previous rules), it will ignore your specific query and default to a general description of the screen.
- **Analysis Depth**: The system uses `gpt-4o-mini` with `high` detail settings to ensure small text and UI elements are legible.

## Response Handling
The tool returns a text analysis prefixed with the monitor index.
- **Local Persistence**: Every time this tool is called, the system saves the raw capture as `latest_screenshot.jpg` in the `.wade/workspace/` directory.
- **API Errors**: If the `OPENAI_API_KEY` is missing or the request fails, the tool will return a specific error string for troubleshooting.