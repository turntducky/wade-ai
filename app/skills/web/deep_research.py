import logging

from app.skills.registry import register_tool
from app.skills.web.web_search import web_search
from app.skills.web.browser import control_browser

logger = logging.getLogger("wade.skills.deep_research")

@register_tool("deep_research")
async def deep_research(topic: str) -> str:
    """Automates a multi-step research and extraction loop."""
    try:
        search_query = f"{topic} documentation examples fix 2026"
        logger.info(f"[DEEP_RESEARCH] Searching for: {search_query}")
        search_results_raw = await web_search(search_query, max_results="5")
        
        import re
        urls = re.findall(r'URL: (https?://\S+)', search_results_raw)
        
        if not urls:
            return f"Deep Research Failed: Could not find any relevant URLs for '{topic}'."

        knowledge_base = [f"### DEEP RESEARCH KNOWLEDGE BASE: {topic}\n"]
        
        for i, url in enumerate(urls[:3], 1):
            logger.info(f"[DEEP_RESEARCH] Extracting from: {url}")
            
            nav_res = await control_browser(action="navigate", visible=False, target=url)
            if "Error" in nav_res:
                knowledge_base.append(f"Source {i} ({url}): Navigation Failed.")
                continue
                
            content_res = await control_browser(action="extract_text", visible=False)
            
            clean_content = re.sub(r'<browser_content.*?>|</browser_content>', '', content_res, flags=re.DOTALL).strip()
            
            knowledge_base.append(f"Source {i} ({url}):\n{clean_content[:2000]}...\n") # Keep it manageable
            
        final_kb = "\n".join(knowledge_base)
        return (
            f"✅ Deep Research Complete for '{topic}'.\n\n"
            f"{final_kb}\n"
            "INSTRUCTION: Use this data to solve the user's problem with high precision."
        )

    except Exception as e:
        return f"❌ Deep Research Error: {str(e)}"