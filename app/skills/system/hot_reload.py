import asyncio
import logging

from app.skills.sdk import wade_tool
from app.core.config import ConfigManager
from app.skills.registry import reload_skills

logger = logging.getLogger("wade.skills.hot_reload")

@wade_tool(
    name="hot_reload_system",
    description="Forces a dynamic reload of all skill modules, configuration, and the semantic tool router.",
    risk="high",
    category="system",
    parameters={},
    required_params=[],
    reversible=True,
    instructions=(
        "Use this tool immediately after editing a Python skill file, updating an .md manifest, "
        "or changing system configuration via ConfigManager. "
        "This tool re-indexes the SkillRouter so changes take effect without restarting the Gateway."
    ),
)
async def hot_reload_system() -> str:
    """Forces a dynamic reload of all skill modules and clears the config cache."""
    try:
        def _reload():
            ConfigManager.reload()

            reload_skills()

            try:
                from app.skills.semantic_router import SkillRouter
                router = SkillRouter()
                router.index_tools()
                indexed = True
            except Exception as e:
                logger.warning(f"Could not re-index tools after hot reload: {e}")
                indexed = False

            msg = "✅ Success: System configuration and all skill modules have been successfully hot-reloaded into memory."
            if indexed:
                msg += " Semantic tool index updated."
            return msg

        return await asyncio.to_thread(_reload)
    except Exception as e:
        return f"❌ Failed to hot-reload system: {str(e)}"