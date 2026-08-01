"""Smoke tests for the MCP-stubbed life tools (Calendar, Email, Notes)."""

import pytest

from collie_core.tools.calendar import CalendarTool
from collie_core.tools.email import EmailTool
from collie_core.tools.notes import NotesTool

# -- Calendar ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_calendar_list_stub() -> None:
    tool = CalendarTool()
    result = await tool.execute(action="list", date_range="today")
    assert "calendar" in str(result).lower()
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_calendar_create_stub() -> None:
    tool = CalendarTool()
    result = await tool.execute(action="create", title="Dentist")
    assert "Dentist" in str(result)
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_calendar_find_free_stub() -> None:
    tool = CalendarTool()
    result = await tool.execute(action="find_free", duration_minutes=60)
    assert "60" in str(result)
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_calendar_unknown_action() -> None:
    tool = CalendarTool()
    result = await tool.execute(action="bark")
    assert "not sure" in str(result).lower()


# -- Email ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_list_stub() -> None:
    tool = EmailTool()
    result = await tool.execute(action="list")
    assert "inbox" in str(result).lower()
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_email_search_stub() -> None:
    tool = EmailTool()
    result = await tool.execute(action="search", query="invoice")
    assert "invoice" in str(result)
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_email_read_stub() -> None:
    tool = EmailTool()
    result = await tool.execute(action="read", email_id="abc123")
    assert "email" in str(result).lower()
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_email_draft_stub() -> None:
    tool = EmailTool()
    result = await tool.execute(action="draft")
    assert "draft" in str(result).lower()
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_email_unknown_action() -> None:
    tool = EmailTool()
    result = await tool.execute(action="fetch")
    assert "not sure" in str(result).lower()


# -- Notes ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notes_create_stub() -> None:
    tool = NotesTool()
    result = await tool.execute(action="create", title="Shopping list")
    assert "Shopping list" in str(result)
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_notes_search_stub() -> None:
    tool = NotesTool()
    result = await tool.execute(action="search", query="recipe")
    assert "recipe" in str(result)
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_notes_list_recent_stub() -> None:
    tool = NotesTool()
    result = await tool.execute(action="list_recent")
    assert "recent notes" in str(result).lower()
    assert "Settings" in str(result)


@pytest.mark.asyncio
async def test_notes_unknown_action() -> None:
    tool = NotesTool()
    result = await tool.execute(action="jump")
    assert "not sure" in str(result).lower()
