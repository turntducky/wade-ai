---
name: whatsapp_lookup_contact
description: Search the synced WhatsApp contact list by name. Returns matching contacts with their phone numbers and JIDs. Use this before sending a message or creating a group when you only have a person's name.
category: whatsapp
requires_network: false
risk: low
reversible: true
cacheable: true
cache_ttl: 120
parameters:
  name:
    type: string
    description: Full or partial contact name to search for (case-insensitive).
required: [name]
---

# whatsapp_lookup_contact

## Instructions

- Always call this before `whatsapp_send_message` or `whatsapp_create_group` when you have a name but not a phone number.
- If multiple matches are returned, ask the user to confirm which one they mean before proceeding.
- Contacts are populated from the bridge's WhatsApp session — only contacts synced to that account will appear.
