"""Tests for display() and the MCP Apps renderer."""

from textwrap import dedent
import pytest
from fastmcp import Client

from parselbox import Parselbox
from parselbox.mcp import RENDERER_URI

pytestmark = pytest.mark.asyncio


async def run(sbx, code):
    result = await sbx.execute_code(dedent(code).strip())
    if not result.is_success:
        raise AssertionError(f"Execution failed: {result.error}")
    return result


class TestDisplay:
    async def test_html_becomes_a_view(self):
        async with Parselbox() as sbx:
            r = await run(sbx, 'display("<h1>Q3</h1><p>up 12%</p>")')
            assert r.view.startswith("<!DOCTYPE html>")
            assert "up 12%" in r.view

    async def test_runtime_is_injected(self):
        async with Parselbox() as sbx:
            r = await run(sbx, 'display("<p>hi</p>")')
            assert "tailwindcss" in r.view
            assert "daisyui" in r.view
            assert "window.pbx" in r.view

    async def test_no_display_no_view(self):
        async with Parselbox() as sbx:
            r = await run(sbx, "1 + 1")
            assert r.view is None

    async def test_display_from_file(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                open("card.html", "w").write("<div>from a file</div>")
                display("card.html")
                """,
            )
            assert "from a file" in r.view

    async def test_missing_file_raises(self):
        async with Parselbox() as sbx:
            r = await sbx.execute_code('display("nope.html")')
            assert not r.is_success
            assert "neither HTML nor an existing file" in r.error

    async def test_last_display_wins(self):
        async with Parselbox() as sbx:
            r = await run(sbx, 'display("<p>first</p>")\ndisplay("<p>second</p>")')
            assert "second" in r.view
            assert "first" not in r.view

    async def test_view_does_not_leak_across_executions(self):
        async with Parselbox() as sbx:
            await run(sbx, 'display("<p>once</p>")')
            r = await run(sbx, "42")
            assert r.view is None


class TestUIGating:
    async def test_on_by_default(self):
        async with Parselbox() as sbx:
            assert sbx.ui is True
            assert "display(" in sbx.get_prompt()

    async def test_disable_ui_hides_display_from_the_agent(self):
        async with Parselbox() as sbx:
            sbx.parselbox_mcp.set_ui(False)
            assert "display(" not in sbx.get_prompt()
            r = await run(sbx, 'display("<b>x</b>")')
            assert r.view is not None

    async def test_renderer_is_registered(self):
        async with Parselbox() as sbx:
            async with Client(sbx.parselbox_mcp.mcp) as client:
                tools = await client.list_tools()
                ui = (tools[0].meta or {}).get("ui", {})
                assert ui["resourceUri"] == RENDERER_URI
                assert "resourceDomains" in ui["csp"]

                resources = await client.list_resources()
                assert RENDERER_URI in [str(r.uri) for r in resources]

    async def test_view_rides_in_structured_content(self):
        async with Parselbox() as sbx:
            async with Client(sbx.parselbox_mcp.mcp) as client:
                out = await client.call_tool(
                    "execute_code", {"code": 'display("<b>hi</b>")'}
                )
                assert "hi" in out.structured_content["view"]

                plain = await client.call_tool("execute_code", {"code": "2 + 2"})
                assert "view" not in plain.structured_content

    async def test_set_ui_false_withholds_the_view(self):
        async with Parselbox() as sbx:
            sbx.parselbox_mcp.set_ui(False)
            async with Client(sbx.parselbox_mcp.mcp) as client:
                out = await client.call_tool(
                    "execute_code", {"code": 'display("<b>hi</b>")'}
                )
                assert "view" not in (out.structured_content or {})

    async def test_disable_ui_drops_the_tool_meta(self):
        async with Parselbox() as sbx:
            sbx.parselbox_mcp.set_ui(False)
            async with Client(sbx.parselbox_mcp.mcp) as client:
                tools = await client.list_tools()
                assert "ui" not in (tools[0].meta or {})
