---
name: whatsapp_send_message
description: Sends a WhatsApp text message to a specific phone number or contact JID. Use this when asked to message, text, or reach out to someone on WhatsApp.
category: whatsapp
requires_network: false
risk: high
reversible: false
parameters:
  recipient:
    type: string
    description: "Phone number (e.g. '+12025551234'), digits-only string, or full JID (e.g. '12025551234@s.whatsapp.net')."
  message:
    type: string
    description: The text message to send.
required: [recipient, message]
---

# whatsapp_send_message

## Instructions

- Use `whatsapp_lookup_contact` first if you only have a name — get the JID, then call this tool.
- Always confirm the recipient and message with the user before sending unless they gave an explicit instruction.
- Do NOT use this for bulk or broadcast messaging — one recipient per call only.
- `reversible: false` means this goes through the approval gate before executing.
