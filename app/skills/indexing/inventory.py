import sqlite3

from pathlib import Path

from app.skills.registry import register_tool

STATE_DB_PATH = Path.home() / ".wade" / "indexer_state.db"

@register_tool("get_knowledge_inventory")
async def get_knowledge_inventory() -> str:
    """Reads the SQLite state database to show the user exactly what is indexed."""
    if not STATE_DB_PATH.exists():
        return "The knowledge base is currently empty or the indexer has not run yet."
        
    try:
        conn = sqlite3.connect(STATE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT filepath FROM files")
        files = cursor.fetchall()
        conn.close()
        
        if not files:
            return "Knowledge base is initialized but is currently empty."
            
        file_list = [f[0] for f in files]
        summary = f"📊 W.A.D.E. Knowledge Inventory:\nTotal Files Indexed: {len(file_list)}\n\n"
        summary += "Recent / Important Paths:\n- " + "\n- ".join(file_list[:15])
        
        if len(file_list) > 15:
            summary += f"\n... and {len(file_list) - 15} more files."
            
        return summary
    except Exception as e:
        return f"Error accessing inventory: {str(e)}"
    
if __name__ == "__main__":
    import asyncio
    
    async def run_test():
        print("Fetching Knowledge Inventory...\n")
        result = await get_knowledge_inventory()
        print(result)
        
    asyncio.run(run_test())