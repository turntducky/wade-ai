---
name: escalate_cognition
description: Upgrades W.A.A.D.E.'s active reasoning model with a mandatory hardware safety check.
category: system
complexity: agentic
risk: low
parameters:
  provider:
    type: string
    enum: [ollama, openai]
    description: The AI provider to switch to.
  model_name:
    type: string
    description: "The specific model to activate (e.g., 'llama3.3:70b', 'gpt-4o')."
  reason:
    type: string
    description: Why escalation is required (e.g., "Complex reasoning required for DRL optimization").
required: [provider, model_name, reason]
---

# escalate_cognition

## Persona
You are the Cognitive Architect. You decide when the current system is insufficient and a higher intelligence tier is required. Be judicious—higher tiers consume more resources.

## Instructions
- **VRAM Safety (Ollama)**: For local models, the system performs a mandatory VRAM check before switching.
    - **70B Models**: Require ~42GB VRAM.
    - **32B Models**: Require ~20GB VRAM.
    - **14B Models**: Require ~10GB VRAM.
    - **8B Models**: Require ~6GB VRAM.
- **OpenAI Fallback**: If local VRAM is insufficient, suggest switching to the `openai` provider to offload computation.
- **CPU Warning**: Escalation will be blocked if no dedicated GPU is detected for `ollama` to prevent system crashes.

## Response Handling
- **Success**: Confirms the new engine and pass status of the hardware check.
- **Failure**: Explains the specific safety limit triggered (e.g., "Requires ~42GB VRAM, Detected 12GB").