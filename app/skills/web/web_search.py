import asyncio

from ddgs import DDGS

from app.skills.registry import register_tool

@register_tool("web_search")
async def web_search(query: str, max_results: str = "3") -> str: 
    """Executes a text-based web search using DuckDuckGo."""
    try:
        try:
            limit = int(float(max_results))
        except (ValueError, TypeError):
            limit = 3
            
        def _search():
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=limit))

        results = await asyncio.to_thread(_search)
        
        if not results:
            return f"No search results found for: '{query}'"
            
        formatted_results = [f"Web Search Results for '{query}':\n"]
        for idx, res in enumerate(results, 1):
            formatted_results.append(
                f"{idx}. Title: {res.get('title')}\n"
                f"   URL: {res.get('href')}\n"
                f"   Snippet: {res.get('body')}\n"
            )
            
        return "\n".join(formatted_results)

    except Exception as e:
        return f"Web Search Error: {str(e)}"
    
if __name__ == "__main__":
    async def run_test():
        print("--- TEST 1: Basic Search ---")
        result = await web_search("latest advancements in Python", max_results=2)
        print(result)

    asyncio.run(run_test())