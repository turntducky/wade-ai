import sys
import pytest

from unittest.mock import AsyncMock, patch

def _fresh_import():
    """Force reimport of the notion skill module."""
    for key in list(sys.modules.keys()):
        if "app.skills.notion" in key:
            del sys.modules[key]
    from app.skills.notion.notion import notion
    return notion

def _mock_creds(token="secret_test"):
    return patch("app.core.credentials.CredentialsManager.get", return_value={"token": token})

def _mock_client(pages_create=None, pages_retrieve=None, pages_update=None,
                  databases_query=None, databases_retrieve=None,
                  blocks_children_list=None, blocks_children_append=None):
    mock = AsyncMock()
    mock.aclose = AsyncMock()
    if pages_create:
        mock.pages.create = AsyncMock(return_value=pages_create)
    if pages_retrieve:
        mock.pages.retrieve = AsyncMock(return_value=pages_retrieve)
    if pages_update:
        mock.pages.update = AsyncMock(return_value=pages_update)
    if databases_query:
        mock.databases.query = AsyncMock(return_value=databases_query)
    if databases_retrieve:
        mock.databases.retrieve = AsyncMock(return_value=databases_retrieve)
    if blocks_children_list:
        mock.blocks.children.list = AsyncMock(return_value=blocks_children_list)
    if blocks_children_append:
        mock.blocks.children.append = AsyncMock(return_value=blocks_children_append)
    return mock

@pytest.mark.asyncio
async def test_returns_install_hint_when_notion_client_missing():
    """If notion-client is not installed, every action returns a pip install hint."""
    for key in list(sys.modules.keys()):
        if "app.skills.notion" in key:
            del sys.modules[key]

    with patch.dict(sys.modules, {"notion_client": None, "notion_client.errors": None}):
        from app.skills.notion.notion import notion
        result = await notion(action="get_page", page_id="abc")

    assert "pip install notion-client" in result

@pytest.mark.asyncio
async def test_returns_not_configured_when_no_credentials():
    with patch("app.core.credentials.CredentialsManager.get", return_value=None):
        notion = _fresh_import()
        result = await notion(action="get_page", page_id="abc123")
    assert "not connected" in result.lower()
    assert "wade setup" in result.lower()

@pytest.mark.asyncio
async def test_create_page_requires_title():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="create_page", parent_page_id="parent_abc")
    assert "title" in result.lower()

@pytest.mark.asyncio
async def test_create_page_requires_parent_or_database():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="create_page", title="My Page")
    assert "parent_page_id" in result or "database_id" in result

@pytest.mark.asyncio
async def test_create_page_under_parent_success():
    resp = {"id": "new_page_id", "url": "https://notion.so/new_page_id"}
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(pages_create=resp)):
            notion = _fresh_import()
            result = await notion(action="create_page", title="My Page", parent_page_id="par_abc")
    assert "My Page" in result
    assert "new_page_id" in result

@pytest.mark.asyncio
async def test_create_page_under_database_success():
    db_resp = {"properties": {"Task Name": {"type": "title"}, "Status": {"type": "select"}}}
    resp = {"id": "db_page_id", "url": "https://notion.so/db_page_id"}
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            databases_retrieve=db_resp,
            pages_create=resp,
        )):
            notion = _fresh_import()
            result = await notion(action="create_page", title="Entry", database_id="db_abc")
    assert "db_page_id" in result

@pytest.mark.asyncio
async def test_get_page_requires_page_id():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="get_page")
    assert "page_id" in result.lower()

@pytest.mark.asyncio
async def test_get_page_returns_title_and_content():
    page_resp = {
        "id": "pg_123",
        "url": "https://notion.so/pg_123",
        "properties": {
            "title": {
                "type": "title",
                "title": [{"plain_text": "My Test Page"}],
            }
        },
    }
    blocks_resp = {
        "results": [
            {
                "type": "paragraph",
                "has_children": False,
                "paragraph": {
                    "rich_text": [{"plain_text": "Hello from Notion."}]
                },
            }
        ]
    }
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            pages_retrieve=page_resp,
            blocks_children_list=blocks_resp,
        )):
            notion = _fresh_import()
            result = await notion(action="get_page", page_id="pg_123")
    assert "My Test Page" in result
    assert "Hello from Notion." in result

@pytest.mark.asyncio
async def test_update_page_requires_page_id():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="update_page", title="New Title")
    assert "page_id" in result.lower()

@pytest.mark.asyncio
async def test_update_page_requires_title_or_properties():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="update_page", page_id="pg_abc")
    assert "title" in result.lower() or "properties" in result.lower()

@pytest.mark.asyncio
async def test_update_page_success():
    page_resp = {
        "id": "pg_abc",
        "url": "https://notion.so/pg_abc",
        "properties": {
            "title": {"type": "title", "title": [{"plain_text": "Old Title"}]}
        },
    }
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            pages_retrieve=page_resp,
            pages_update={"id": "pg_abc"},
        )):
            notion = _fresh_import()
            result = await notion(action="update_page", page_id="pg_abc", title="New Title")
    assert "Updated" in result or "pg_abc" in result

@pytest.mark.asyncio
async def test_create_db_entry_requires_database_id_and_title():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="create_db_entry", title="Task A")
    assert "database_id" in result.lower()

@pytest.mark.asyncio
async def test_create_db_entry_success():
    db_resp = {
        "properties": {
            "Name": {"type": "title"},
        }
    }
    page_resp = {"id": "entry_abc", "url": "https://notion.so/entry_abc"}
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            databases_retrieve=db_resp,
            pages_create=page_resp,
        )):
            notion = _fresh_import()
            result = await notion(action="create_db_entry", database_id="db_abc", title="Task A")
    assert "Task A" in result
    assert "entry_abc" in result

@pytest.mark.asyncio
async def test_query_database_requires_database_id():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="query_database")
    assert "database_id" in result.lower()

@pytest.mark.asyncio
async def test_query_database_invalid_filter_json():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="query_database", database_id="db_abc", filter="{bad json")
    assert "Invalid JSON" in result

@pytest.mark.asyncio
async def test_query_database_returns_entries():
    db_resp = {
        "results": [
            {
                "id": "row1",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Task One"}]}
                },
            },
            {
                "id": "row2",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Task Two"}]}
                },
            },
        ]
    }
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(databases_query=db_resp)):
            notion = _fresh_import()
            result = await notion(action="query_database", database_id="db_abc")
    assert "Task One" in result
    assert "Task Two" in result

@pytest.mark.asyncio
async def test_query_database_empty_result():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            databases_query={"results": []}
        )):
            notion = _fresh_import()
            result = await notion(action="query_database", database_id="db_abc")
    assert "No entries" in result

@pytest.mark.asyncio
async def test_append_blocks_requires_page_id_and_content():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="append_blocks", content="Hello")
    assert "page_id" in result.lower()

@pytest.mark.asyncio
async def test_append_blocks_success():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            blocks_children_append={}
        )):
            notion = _fresh_import()
            result = await notion(
                action="append_blocks",
                page_id="pg_abc",
                content="First paragraph\n\nSecond paragraph",
            )
    assert "2" in result or "block" in result.lower()

@pytest.mark.asyncio
async def test_get_block_children_requires_page_id():
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client()):
            notion = _fresh_import()
            result = await notion(action="get_block_children")
    assert "page_id" in result.lower()

@pytest.mark.asyncio
async def test_get_block_children_returns_text():
    blocks_resp = {
        "results": [
            {
                "type": "heading_1",
                "id": "blk1",
                "has_children": False,
                "heading_1": {
                    "rich_text": [{"plain_text": "Section One"}]
                },
            },
            {
                "type": "paragraph",
                "id": "blk2",
                "has_children": False,
                "paragraph": {
                    "rich_text": [{"plain_text": "Body text here."}]
                },
            },
        ]
    }
    with _mock_creds():
        with patch("notion_client.AsyncClient", return_value=_mock_client(
            blocks_children_list=blocks_resp
        )):
            notion = _fresh_import()
            result = await notion(action="get_block_children", page_id="pg_abc")
    assert "Section One" in result
    assert "Body text here." in result

@pytest.mark.asyncio
async def test_404_returns_connection_hint():
    from notion_client.errors import APIResponseError
    import httpx
    err = APIResponseError(
        code="object_not_found",
        status=404,
        message="Could not find page",
        headers=httpx.Headers({}),
        raw_body_text="",
    )

    with _mock_creds():
        mock = _mock_client()
        mock.pages.retrieve = AsyncMock(side_effect=err)
        with patch("notion_client.AsyncClient", return_value=mock):
            notion = _fresh_import()
            result = await notion(action="get_page", page_id="pg_abc")
    assert "Connect to" in result or "connect" in result.lower()

@pytest.mark.asyncio
async def test_401_returns_token_hint():
    from notion_client.errors import APIResponseError
    import httpx
    err = APIResponseError(
        code="unauthorized",
        status=401,
        message="Unauthorized",
        headers=httpx.Headers({}),
        raw_body_text="",
    )

    with _mock_creds():
        mock = _mock_client()
        mock.pages.retrieve = AsyncMock(side_effect=err)
        with patch("notion_client.AsyncClient", return_value=mock):
            notion = _fresh_import()
            result = await notion(action="get_page", page_id="pg_abc")
    assert "invalid" in result.lower() or "wade setup" in result.lower()