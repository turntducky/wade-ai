import logging

from datetime import datetime

from app.skills.sdk import wade_tool

logger = logging.getLogger("wade.skills.time")

@wade_tool(
    name="get_current_time",
    description="Retrieve the current time and date for the system or a specific global location.",
    risk="low",
    category="system",
    cacheable=True,
    cache_ttl=30,
    parameters={
        "location": {
            "type": "string",
            "description": "Optional: The city or country (e.g., 'London', 'Tokyo'). If blank, returns local time.",
        }
    },
    required_params=[],
    instructions=(
        "Returns the current system time, day of the week, and full date. "
        "If a location is provided, performs a web search to find the current time in that region. "
        "Use to timestamp logs, schedule tasks, or provide international context."
    ),
)
async def get_current_time(location: str | None = None) -> str:
    """Returns the current time, optionally for a specific location."""
    if not location:
        now = datetime.now()
        return f"The current local time is {now.strftime('%I:%M %p')} on {now.strftime('%A, %B %d, %Y')}."

    from app.skills.web.web_search import web_search

    query = f"current time in {location}"
    logger.info(f"Searching for time in {location}...")

    search_result = await web_search(query)

    return f"I've searched for the time in {location}:\n{search_result}"