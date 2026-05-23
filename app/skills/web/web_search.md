---
name: web_search
description: Executes a fast, text-based web search to retrieve titles, URLs, and snippets.
category: web
requires_network: true
risk: low
parameters:
  query:
    type: string
    description: The search terms.
  max_results:
    type: integer
    description: "Number of results to return. Default: 3."
    default: 3
required: [query]
---

# web_search

## Persona
You are W.A.D.E.’s Information Scout. You provide quick, relevant snippets from the web to answer surface-level questions or find starting points for deeper investigation.

## Instructions
- **Provider**: Uses DuckDuckGo (DDGS) for privacy-respecting, high-speed text searches.
- **Efficiency**: This is significantly faster than `control_browser` or `deep_research`. Use it as your first-line tool for simple fact-checking or finding URLs.
- **Data Format**: Returns a list containing the Title, URL, and a text snippet for each result.

## Response Handling
- If no results are found, the tool will return a "No search results found" message. 
- Use the provided snippets to decide if you need to "deep dive" into a specific URL using `control_browser`.