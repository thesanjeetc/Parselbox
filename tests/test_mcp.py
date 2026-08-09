import json

import pytest
from fastmcp import Client
from fastmcp.client.elicitation import ElicitResult

from parselbox import Parselbox
from parselbox.bridge import Bridge

pytestmark = pytest.mark.asyncio


def accept_all():
    async def handler(message, response_type, params, context):
        return ElicitResult(action="accept")

    return handler


def accept_and_record():
    events = []

    async def handler(message, response_type, params, context):
        events.append(json.loads(message))
        return ElicitResult(action="accept")

    return handler, events


class TestMCPExecution:
    async def test_basic_execution(self):
        async with Parselbox() as sandbox:
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=accept_all())
            async with client:
                result = await client.call_tool("execute_code", {"code": "1 + 1"})
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "2" in text

    async def test_error_returns_error_dict(self):
        async with Parselbox() as sandbox:
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=accept_all())
            async with client:
                result = await client.call_tool(
                    "execute_code", {"code": "raise ValueError('boom')"}
                )
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "boom" in text

    async def test_none_output_returns_guidance(self):
        async with Parselbox() as sandbox:
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=accept_all())
            async with client:
                result = await client.call_tool(
                    "execute_code", {"code": "print('hello')"}
                )
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "no output" in text.lower()

    async def test_large_output_truncated(self):
        async with Parselbox() as sandbox:
            sandbox.parselbox_mcp.max_tokens_estimate = 10
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=accept_all())
            async with client:
                result = await client.call_tool("execute_code", {"code": "'x' * 1000"})
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "too big" in text.lower()


class TestMCPResources:
    async def test_created_files_registered_as_resources(self):
        async with Parselbox() as sandbox:
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=accept_all())
            async with client:
                result = await client.call_tool(
                    "execute_code",
                    {"code": "open('output.txt', 'w').write('hello'); 'done'"},
                )
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "done" in text

                resources = await client.list_resources()
                assert any("output.txt" in str(r.uri) for r in resources)


class TestMCPElicitation:
    async def test_elicit_approve(self):
        handler, events = accept_and_record()

        async with Parselbox() as sandbox:
            sandbox.parselbox_mcp.enable_elicit()
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=handler)
            async with client:
                result = await client.call_tool("execute_code", {"code": "1 + 1"})
                assert any("2" in c.text for c in result.content if hasattr(c, "text"))

        assert any(e["hook"] == "pre_execute" for e in events)
        assert any(e["hook"] == "post_execute" for e in events)

    async def test_elicit_deny(self):
        async def deny_handler(message, response_type, params, context):
            return ElicitResult(action="decline")

        async with Parselbox() as sandbox:
            sandbox.parselbox_mcp.enable_elicit()
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=deny_handler)
            async with client:
                result = await client.call_tool("execute_code", {"code": "1 + 1"})
                text = " ".join(c.text for c in result.content if hasattr(c, "text"))
                assert "error" in text.lower() or "blocked" in text.lower()

    async def test_elicit_tool_call(self):
        handler, events = accept_and_record()

        class Greeter(Bridge):
            def greet(self, name: str) -> str:
                """Greet someone."""
                return f"Hello {name}"

        async with Parselbox(context={"greeter": Greeter()}) as sandbox:
            sandbox.parselbox_mcp.enable_elicit()
            client = Client(sandbox.parselbox_mcp.mcp, elicitation_handler=handler)
            async with client:
                await client.call_tool(
                    "execute_code", {"code": "greeter.greet(name='World')"}
                )

        hook_names = [e["hook"] for e in events]
        assert "pre_execute" in hook_names
        assert "pre_tool_call" in hook_names
        assert "post_tool_call" in hook_names
        assert "post_execute" in hook_names
