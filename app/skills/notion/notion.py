from __future__ import annotations

import json

from typing import Optional, TYPE_CHECKING

from app.core.credentials import CredentialsManager
from app.skills.registry import register_tool, SkillManifest

if TYPE_CHECKING:
    from notion_client import AsyncClient
    from notion_client.errors import APIResponseError
    _NOTION_AVAILABLE = True

else:
    try:
        from notion_client import AsyncClient
        from notion_client.errors import APIResponseError
        _NOTION_AVAILABLE = True
    except ImportError:
        _NOTION_AVAILABLE = False
        AsyncClient = None
        
        class APIResponseError(Exception):
            status: int
            body: str

def _title_property_for_page(title: str) -> list:
    """Notion title property format for standalone pages."""
    return [{"text": {"content": title}}]

def _title_property_for_db(title: str) -> dict:
    """Notion title property format for database entries."""
    return {"title": [{"text": {"content": title}}]}

def _extract_title(props: dict) -> str:
    """Pull plain text from whichever property has type=title."""
    for v in props.values():
        if v.get("type") == "title":
            return "".join(rt.get("plain_text", "") for rt in v.get("title", []))
    return "(untitled)"

def _block_to_text(block: dict) -> str:
    """Convert a single Notion block to a plain-text line."""
    btype = block.get("type", "")
    content = block.get(btype, {})
    rich_text = content.get("rich_text", [])
    text = "".join(rt.get("plain_text", "") for rt in rich_text)
    if not text:
        return ""
    prefixes = {
        "heading_1": "# ",
        "heading_2": "## ",
        "heading_3": "### ",
        "bulleted_list_item": "• ",
        "numbered_list_item": "• ",
        "to_do": ("✓ " if content.get("checked") else "☐ "),
    }
    return prefixes.get(btype, "") + text

def _format_properties(props: dict) -> str:
    """Render Notion properties dict as a human-readable string."""
    lines = []
    type_extractors = {
        "title":        lambda v: "".join(rt.get("plain_text", "") for rt in v.get("title", [])),
        "rich_text":    lambda v: "".join(rt.get("plain_text", "") for rt in v.get("rich_text", [])),
        "select":       lambda v: (v.get("select") or {}).get("name", ""),
        "multi_select": lambda v: ", ".join(s.get("name", "") for s in v.get("multi_select", [])),
        "checkbox":     lambda v: "✓" if v.get("checkbox") else "☐",
        "date":         lambda v: (v.get("date") or {}).get("start", ""),
        "number":       lambda v: str(v.get("number", "")),
        "url":          lambda v: v.get("url", ""),
        "email":        lambda v: v.get("email", ""),
    }
    for name, val in props.items():
        ptype = val.get("type", "")
        if ptype == "title":
            continue
        extractor = type_extractors.get(ptype)
        if extractor:
            text = extractor(val)
            if text:
                lines.append(f"  {name}: {text}")
    return "\n".join(lines) or "  (no readable properties)"

def _parse_json_param(value: str, param_name: str):
    """Parse a JSON string parameter. Returns (parsed, error_string)."""
    try:
        return json.loads(value), None
    except json.JSONDecodeError as e:
        return None, f"Invalid JSON in {param_name}: {e}"

async def _create_page(client, title, parent_page_id, database_id, properties_json):
    if not title:
        return "create_page requires a 'title'."
    if not parent_page_id and not database_id:
        return "create_page requires either 'parent_page_id' or 'database_id'."

    if database_id:
        db = await client.databases.retrieve(database_id=database_id)
        db_props = db.get("properties", {}) if isinstance(db, dict) else {}
        title_key = next(
            (k for k, v in db_props.items() if v.get("type") == "title"),
            "Name",
        )
        props = {title_key: _title_property_for_db(title)}
        parent = {"database_id": database_id}
    else:
        props = {"title": _title_property_for_page(title)}
        parent = {"page_id": parent_page_id}

    if properties_json:
        extra, err = _parse_json_param(properties_json, "properties")
        if err:
            return err
        if isinstance(extra, dict):
            props.update(extra)

    result = await client.pages.create(parent=parent, properties=props)
    return f"Created page '{title}'. ID: {result['id']} | {result['url']}"

async def _get_page(client, page_id):
    if not page_id:
        return "get_page requires 'page_id'."
    page = await client.pages.retrieve(page_id=page_id)
    title = _extract_title(page.get("properties", {}))
    props_text = _format_properties(page.get("properties", {}))
    blocks_resp = await client.blocks.children.list(block_id=page_id)
    lines = [_block_to_text(b) for b in blocks_resp.get("results", [])]
    body = "\n".join(l for l in lines if l) or "(empty)"
    if blocks_resp.get("has_more"):
        body += "\n\n[Content truncated — page has more than 100 blocks. Use get_block_children for full content.]"
    return f"Page: {title}\nURL: {page['url']}\n\nProperties:\n{props_text}\n\nContent:\n{body}"

async def _update_page(client, page_id, title, properties_json):
    if not page_id:
        return "update_page requires 'page_id'."
    if not title and not properties_json:
        return "update_page requires at least 'title' or 'properties'."

    props = {}
    if title:
        page = await client.pages.retrieve(page_id=page_id)
        title_key = next(
            (k for k, v in page.get("properties", {}).items() if v.get("type") == "title"),
            "title",
        )
        props[title_key] = _title_property_for_db(title)

    if properties_json:
        extra, err = _parse_json_param(properties_json, "properties")
        if err:
            return err
        if isinstance(extra, dict):
            props.update(extra)

    await client.pages.update(page_id=page_id, properties=props)
    updated = []
    if title:
        updated.append(f"title → '{title}'")
    if properties_json:
        updated.append("properties updated")
    return f"Updated page {page_id}: {', '.join(updated)}."

async def _create_db_entry(client, database_id, title, properties_json):
    if not database_id:
        return "create_db_entry requires 'database_id'."
    if not title:
        return "create_db_entry requires 'title'."

    db = await client.databases.retrieve(database_id=database_id)
    db_props = db.get("properties", {}) if isinstance(db, dict) else {}
    title_key = next(
        (k for k, v in db_props.items() if v.get("type") == "title"),
        "Name",
    )
    props = {title_key: _title_property_for_db(title)}

    if properties_json:
        extra, err = _parse_json_param(properties_json, "properties")
        if err:
            return err
        if isinstance(extra, dict):
            props.update(extra)

    result = await client.pages.create(
        parent={"database_id": database_id}, properties=props
    )
    return f"Created entry '{title}' in database. ID: {result['id']} | {result['url']}"

async def _query_database(client, database_id, filter_json, sorts_json, limit):
    if not database_id:
        return "query_database requires 'database_id'."

    kwargs: dict = {"database_id": database_id, "page_size": min(limit, 100)}

    if filter_json:
        parsed, err = _parse_json_param(filter_json, "filter")
        if err:
            return err
        kwargs["filter"] = parsed

    if sorts_json:
        parsed, err = _parse_json_param(sorts_json, "sorts")
        if err:
            return err
        kwargs["sorts"] = parsed

    response = await client.databases.query(**kwargs)
    results = response.get("results", [])
    if not results:
        return "No entries found matching your query."

    lines = [f"Found {len(results)} entr{'y' if len(results) == 1 else 'ies'}:"]
    for page in results:
        entry_title = _extract_title(page.get("properties", {}))
        lines.append(f"  • {entry_title}  (ID: {page['id']})")
    return "\n".join(lines)

async def _append_blocks(client, page_id, content):
    if not page_id:
        return "append_blocks requires 'page_id'."
    if not content:
        return "append_blocks requires 'content'."

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    children = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": p}}]
            },
        }
        for p in paragraphs
    ]
    await client.blocks.children.append(block_id=page_id, children=children)
    return f"Appended {len(children)} block(s) to page {page_id}."

async def _get_block_children(client, page_id):
    if not page_id:
        return "get_block_children requires 'page_id'."

    response = await client.blocks.children.list(block_id=page_id)
    blocks = response.get("results", [])
    all_lines = []

    for block in blocks:
        text = _block_to_text(block)
        if text:
            all_lines.append(text)
        if block.get("has_children"):
            sub_resp = await client.blocks.children.list(block_id=block["id"])
            for sub in sub_resp.get("results", []):
                sub_text = _block_to_text(sub)
                if sub_text:
                    all_lines.append("  " + sub_text)

    result = "\n".join(all_lines) if all_lines else "(Page has no text content)"
    if response.get("has_more"):
        result += "\n\n[Content truncated — page has more than 100 blocks at the top level.]"
    return result

@register_tool("notion")
async def notion(action: str, page_id: Optional[str] = None, database_id: Optional[str] = None, parent_page_id: Optional[str] = None, title: Optional[str] = None,
    properties: Optional[str] = None, content: Optional[str] = None, filter: Optional[str] = None, sorts: Optional[str] = None, limit: int = 20) -> str:
    if not _NOTION_AVAILABLE or AsyncClient is None:
        return "notion-client is not installed. Run: pip install notion-client"

    creds = CredentialsManager.get("notion")
    if not creds or not creds.get("token"):
        return "Notion is not connected. Run 'wade setup' to add your integration token."

    client = AsyncClient(auth=creds["token"])
    try:
        if action == "create_page":
            return await _create_page(client, title, parent_page_id, database_id, properties)
        elif action == "get_page":
            return await _get_page(client, page_id)
        elif action == "update_page":
            return await _update_page(client, page_id, title, properties)
        elif action == "create_db_entry":
            return await _create_db_entry(client, database_id, title, properties)
        elif action == "query_database":
            return await _query_database(client, database_id, filter, sorts, limit)
        elif action == "append_blocks":
            return await _append_blocks(client, page_id, content)
        elif action == "get_block_children":
            return await _get_block_children(client, page_id)
        else:
            return f"Unknown action: '{action}'"
    except APIResponseError as e:
        return _handle_error(e)
    except Exception as e:
        return _handle_error(e)
    finally:
        if client:
            await client.aclose()

def _handle_error(e: Exception) -> str:
    if isinstance(e, APIResponseError):
        if e.status == 404:
            return (
                "Could not access that page. Make sure you've connected your W.A.D.E. "
                "integration to it in Notion (open the page → '...' → 'Connect to')."
            )
        if e.status == 401:
            return "Notion token is invalid. Run 'wade setup' to update it."
        if e.status == 429:
            return "Notion rate limit hit. Please try again in a moment."
        return f"Notion API error: {e.status} — {e.body}"
    return f"Notion error: {e}"