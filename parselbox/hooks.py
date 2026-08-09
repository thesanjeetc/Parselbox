import json
from dataclasses import asdict
from typing import Any

from mcp.types import ClientCapabilities

from .models import Callback, ExecutionResult


class Hook:
    async def pre_execute(self, code: str):
        pass

    async def post_execute(self, result: ExecutionResult):
        pass

    async def pre_tool_call(self, callback: Callback):
        pass

    async def post_tool_call(self, callback: Callback, result: Any):
        pass


class ElicitHook(Hook):
    def __init__(self):
        self._session = None
        self._request_id = None
        self._enabled = False

    def set_context(self, ctx):
        self._session = ctx.session
        self._request_id = ctx.request_id
        self._enabled = self._session.check_client_capability(
            ClientCapabilities(elicitation={})
        )

    async def _elicit(self, event: dict):
        if not self._enabled:
            return
        result = await self._session.elicit(
            message=json.dumps(event),
            requestedSchema={
                "type": "object",
                "properties": {
                    "allow": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
            },
            related_request_id=self._request_id,
        )
        if result.action != "accept":
            raise PermissionError("Denied")
        content = result.content or {}
        if content.get("allow") is False:
            raise PermissionError(content.get("reason", "Denied"))

    async def pre_execute(self, code: str):
        await self._elicit({"hook": "pre_execute", "code": code})

    async def post_execute(self, result: ExecutionResult):
        await self._elicit({"hook": "post_execute", "result": asdict(result)})

    async def pre_tool_call(self, callback: Callback):
        await self._elicit({"hook": "pre_tool_call", "callback": asdict(callback)})

    async def post_tool_call(self, callback: Callback, result: Any):
        await self._elicit(
            {
                "hook": "post_tool_call",
                "callback": asdict(callback),
                "result": result,
            }
        )
