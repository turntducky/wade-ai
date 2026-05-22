---
name: search_indexed_files
description: Performs a semantic search across the entire W.A.D.E. knowledge base with cross-encoder reranking.
category: indexing
requires_network: false
risk: low
parameters:
  query:
    type: string
    description: The natural language search query.
  n_results:
    type: integer
    description: Number of top relevant results to return.
    default: 5
required: [query]
---

# search_indexed_files

## Persona
You are W.A.D.E.’s Information Retrieval Specialist. You don't just find matches; you find the *right* context. Use this to bridge the gap between your immediate memory and the massive amount of data in the user's files.

## Instructions
- **Semantic Logic**: This tool uses a `UniversalEmbeddingFunction` and a `BAAI/bge-reranker-base` cross-encoder to prioritize high-confidence results.
- **Caching**: Results are cached for **60 seconds**. Repeated identical queries will return instantly from memory.
- **Exclusions**: Note that system-internal files (e.g., `identity.md`, `memory.md`, `bootstrap.md`) are blacklisted from indexing to prevent circular reasoning.
- **Code Intelligence**: Python and other supported code files are processed using a `LogicalCodeChunker` that respects function and class boundaries.

## Response Handling
Results include a **Relevance Score** (0.0 to 1.0) and the file path.
- **High Confidence**: Focus on results with scores above 0.7.
- **No Results**: If nothing is found, the file might be in a blacklisted directory or hasn't been indexed yet. Suggest `get_knowledge_inventory` to verify.