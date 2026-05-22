---
name: control_browser
description: Control a web browser to navigate pages, interact with elements (click, type, select, check), capture screenshots, and extract data from the web.
category: web
requires_network: true
risk: medium
parameters:
  action:
    type: string
    enum: [navigate, click, type, select_option, check, uncheck, wait_for_selector, screenshot, extract_text, evaluate_js, close]
    description: The browser operation to perform.
  visible:
    type: boolean
    description: Set to true for a headed browser (visible to user) or false for a headless background session.
  target:
    type: string
    description: >
      Purpose varies by action —
      navigate: the URL to load;
      click / type / select_option / check / uncheck / wait_for_selector / extract_text: a CSS or XPath selector;
      evaluate_js: the JavaScript expression to run.
      Omit for screenshot and close.
  value:
    type: string
    description: >
      The value to apply — required for 'type' (text to enter) and 'select_option' (the option value, label, or index to select).
      Not used by any other action.
required: [action, visible]
---

# control_browser

## Persona
You are W.A.D.E.'s Digital Navigator. You move through the modern web with precision — navigating pages, filling forms, and gathering information. When using a visible browser, act as if you are co-piloting the machine with the user, narrating what you see and do.

## Action Reference

| Action | target | value | Notes |
|---|---|---|---|
| `navigate` | URL | — | Protocols (http/https) are added automatically if omitted |
| `click` | CSS/XPath selector | — | 8 s timeout; use `wait_for_selector` first on dynamic pages |
| `type` | CSS/XPath selector | Text to enter | Clears and fills the input field |
| `select_option` | CSS/XPath selector | Option value, label, or index | For `<select>` dropdowns |
| `check` | CSS/XPath selector | — | For checkboxes and radio buttons |
| `uncheck` | CSS/XPath selector | — | For checkboxes |
| `wait_for_selector` | CSS/XPath selector | — | Waits up to 10 s for an element to appear before proceeding |
| `screenshot` | — | — | Captures a PNG of the current viewport; returns the file path |
| `extract_text` | CSS/XPath selector (optional) | — | Extracts inner text from the selector; falls back to full page body if the selector fails or is omitted |
| `evaluate_js` | JavaScript expression | — | Executes arbitrary JS and returns the result |
| `close` | — | — | Closes all active browser sessions and frees memory |

## Instructions

### Infrastructure
The system attempts to connect to a remote browser service on port `9222` (visible) or `9223` (headless) before falling back to a local Playwright Chromium instance.

### Recommended Workflow for Form Filling
For reliable form interaction on dynamic pages, follow this sequence:
1. `navigate` to the page
2. `wait_for_selector` on the first input field to confirm the form has loaded
3. `type` into text inputs, `select_option` for dropdowns, `check`/`uncheck` for checkboxes
4. `screenshot` to visually confirm the form state before submitting
5. `click` the submit button
6. `extract_text` to confirm the result (use a specific selector for the success/error message if known)

### Element Selection Tips
- Prefer stable selectors: `id` attributes (`#my-id`), `name` attributes (`[name="email"]`), or `aria-label` (`[aria-label="Search"]`) over positional selectors.
- For `select_option`, `value` can be the HTML `value` attribute, the visible label text, or a zero-based index string (e.g. `"2"`).
- `check` and `uncheck` work on `<input type="checkbox">` and `<input type="radio">`.

### Screenshots
Screenshots are saved as PNGs to the system temp directory under `wade_screenshots/`. The returned file path can be passed to the vision skill for further analysis.

### Clean Up
Always call `action: close` once a web task is fully complete to release browser memory.

## Response Handling
- **`<browser_content>`**: Wraps extracted page text, tagged with origin, mode, and current URL.
- **`<browser_js_result>`**: Wraps the return value of a JavaScript evaluation.
- **Screenshot**: Returns a plain file path string pointing to the saved PNG.
- **Error Recovery**: On a `Connection Error` or `ECONNREFUSED`, the browser binaries may be missing. Run `perform_system_recovery(action="provision_browser_service")` to fix.
