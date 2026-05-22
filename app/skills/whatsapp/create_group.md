---
name: whatsapp_create_group
description: Creates a new WhatsApp group chat with a given name and list of participants. Participants can be specified by name (resolved from contacts), phone number, JID, or the special keyword "me" (resolves to the admin's own number).
category: whatsapp
requires_network: false
risk: high
reversible: false
parameters:
  group_name:
    type: string
    description: The display name for the new group.
  participants:
    type: array
    items:
      type: string
    description: "List of participants. Each entry can be: a contact name (e.g. 'Bob'), a phone number (e.g. '+12025551234'), a JID, or 'me' to include yourself."
required: [group_name, participants]
---

# whatsapp_create_group

## Instructions

- The bridge account (bot) is automatically added as the group creator — do NOT include it in the participants list.
- Use `"me"` to include the admin's own phone number in the group.
- If a participant name is ambiguous (multiple contacts match), the skill will return an error and ask for clarification — pass phone numbers instead.
- `reversible: false` means this goes through the approval gate before executing.
- Do NOT create duplicate groups — check with the user if unsure whether the group already exists.
