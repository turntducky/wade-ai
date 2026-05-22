import logging

from typing import List, Any

from app.skills.registry import get_tool_descriptions, TOOL_INVENTORY

logger = logging.getLogger("wade.skill_router")

_tools_indexed: bool = False

def invalidate_tool_index() -> None:
    global _tools_indexed
    _tools_indexed = False

class SkillRouter:
    """Handles semantic discovery of skills based on user intent."""
    def __init__(self, chroma_client: Any = None):
        self.chroma_client = chroma_client
        self.collection = None
        if self.chroma_client:
            try:
                self.collection = self.chroma_client.get_or_create_collection(name="wade_skills_index")
            except Exception as e:
                logger.error(f"Failed to initialize skills collection: {e}")

    def index_tools(self):
        """Indexes all tools into the vector store. No-op after first successful index."""
        global _tools_indexed
        if _tools_indexed or not self.collection:
            return

        tools = get_tool_descriptions()
        if not tools:
            return

        ids = [t["name"] for t in tools]
        documents = [f"{t['name']}: {t['description']} (Category: {t['category']})" for t in tools]
        metadatas = [{"name": t["name"], "category": t["category"]} for t in tools]

        try:
            self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            logger.debug(f"Indexed {len(tools)} tools for semantic discovery.")
            _tools_indexed = True
        except Exception as e:
            logger.error(f"Error indexing tools: {e}")

    def get_relevant_tools(self, query: str, n_results: int = 5) -> List[str]:
        """Returns names of tools relevant to the query with a similarity threshold."""
        if not self.collection or not query.strip():
            return []

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results
            )
            
            ids = results.get("ids", [[]])[0]
            distances = results.get("distances", [[]])[0]
            
            DISTANCE_THRESHOLD = 2.0
            relevant = []
            for i, dist in enumerate(distances):
                if dist < DISTANCE_THRESHOLD:
                    relevant.append(ids[i])
                    
            return relevant
        except Exception as e:
            logger.error(f"Error querying relevant tools: {e}")
            return []

def get_relevant_tools_context(query: str, chroma_client: Any = None) -> str:
    """Returns a formatted string of relevant tool descriptions and sidecar instructions to inject into the prompt."""
    router = SkillRouter(chroma_client)
    router.index_tools()

    relevant_names = router.get_relevant_tools(query)
    all_descriptions = {t["name"]: t for t in get_tool_descriptions()}

    tool_lines = []
    instruction_blocks = []

    for name in relevant_names:
        if name not in all_descriptions:
            continue
        t = all_descriptions[name]
        tool_lines.append(f"- {t['name']}: {t['description']}")

        entry = TOOL_INVENTORY.get(name, {})
        manifest = entry.get("manifest")
        if manifest and manifest.instructions:
            instruction_blocks.append(
                f"### {name}\n{manifest.instructions}"
            )

    if not tool_lines:
        return ""

    parts = [
        "<available_tools_summary>",
        "You have the following tools available that might be relevant to this request:",
        "\n".join(tool_lines),
        "</available_tools_summary>",
    ]

    if instruction_blocks:
        parts += [
            "",
            "<tool_instructions>",
            "Behavioral instructions for the tools above — follow these exactly:",
            "",
            "\n\n".join(instruction_blocks),
            "</tool_instructions>",
        ]

    return "\n".join(parts)