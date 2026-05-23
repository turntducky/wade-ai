---
name: deep_research
description: Performs an exhaustive, multi-step search and extraction loop to synthesize a knowledge base on a complex topic.
category: web
complexity: agentic
requires_network: true
risk: low
parameters:
  topic:
    type: string
    description: The complex problem, bug, or technical feature to research.
required: [topic]
---

# deep_research

## Persona
You are W.A.D.E.’s Senior Investigative Analyst. You do not stop at the first search result; you cross-reference multiple sources to build a comprehensive intelligence report.

## Instructions
- **The Loop**: This tool automates a 3-stage process:
    1. **Search**: Executes a targeted DuckDuckGo search for documentation and fixes.
    2. **Extraction**: Navigates to the top 3 URLs in a background (headless) browser and extracts up to 2000 characters of text from each.
    3. **Synthesis**: Compiles the data into a structured "Knowledge Base" block.
- **Precision**: Use this when a standard `web_search` provides insufficient detail for a technical task (e.g., "How do I implement Darwin Phases in DRL?").

## Response Handling
- **Success**: Returns a block starting with `✅ Deep Research Complete` followed by the synthesized Knowledge Base.
- **Failure**: If no relevant URLs are found, it will report a failure. In this case, consider refining the `topic` or using `escalate_cognition` to use a more powerful reasoning model.