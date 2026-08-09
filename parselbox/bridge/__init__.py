import asyncio
import base64
import contextvars
import functools
import inspect
import json
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastmcp.tools.function_tool import FunctionTool
from mcp.types import Tool

from parselbox.models import SearchItem

MAX_NAMESPACE_DEPTH = 5


@dataclass
class TaskContext:
    task_id: str
    log_path: Path
    queue: asyncio.Queue
    started_at: float

    def write_log(self, msg: str):
        with open(self.log_path, "a") as f:
            f.write(msg + "\n")

    def read_queue(self):
        msgs = []
        while not self.queue.empty():
            msgs.append(self.queue.get_nowait())
        return msgs


_current_task = contextvars.ContextVar("current_task", default=None)


def _encode_bytes(obj):
    """Tag bytes as {"__b64__": ...} so tool results survive the JSON boundary.

    The sandbox decodes the marker back to bytes (ParselboxRpc.call), mirroring
    how sandbox-side bytes arguments reach the host (models._deserialize).
    """
    if isinstance(obj, (bytes, bytearray)):
        return {"__b64__": base64.b64encode(bytes(obj)).decode()}
    if isinstance(obj, dict):
        return {k: _encode_bytes(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode_bytes(v) for v in obj]
    return obj


def _encode_bytes_result(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return _encode_bytes(await fn(*args, **kwargs))

    return wrapper


class Bridge:
    _pbx_sandbox = None
    _pbx_tools = None
    _pbx_tool_index = None
    _pbx_target = None
    _pbx_type = "namespace"

    def log(self, msg: str):
        ctx = _current_task.get()
        if ctx:
            ctx.write_log(msg)

    def recv(self):
        ctx = _current_task.get()
        if not ctx:
            return []
        return ctx.read_queue()

    async def _pbx_connect(self):
        if self._pbx_tools is None:
            self._pbx_build_tools()

    async def _pbx_close(self):
        self._pbx_sandbox = None
        self._pbx_tools = None
        self._pbx_tool_index = None

    async def __aenter__(self):
        await self._pbx_connect()
        return self

    async def __aexit__(self, *args):
        await self._pbx_close()

    _pbx_hidden = frozenset({"log", "recv", "wrap"})

    def __dir__(self):
        if self._pbx_target is not None:
            return sorted(a for a in dir(self._pbx_target) if not a.startswith("_"))
        attrs = {k for k, v in self.__dict__.items() if isinstance(v, Bridge)}
        for k in object.__dir__(self):
            if k.startswith("_") or k in self._pbx_hidden:
                continue
            if callable(getattr(type(self), k, None)):
                attrs.add(k)
        return sorted(attrs)

    def _pbx_get_tool(self, path) -> Tool | None:
        if not self._pbx_tools:
            return None
        tool = self._pbx_tools.get(path)
        if not tool:
            return None
        return tool.to_mcp_tool()

    async def _pbx_dispatch(self, path, kwargs, args=None):
        if self._pbx_tools is None:
            raise ConnectionError("Bridge not connected")
        if not self._pbx_get_tool(path):
            raise AttributeError(f"Tool '{path}' not found")
        if args:
            bound = inspect.signature(self._pbx_tools[path].fn).bind(*args, **kwargs)
            bound.apply_defaults()
            kwargs = dict(bound.arguments)
        try:
            return self._unwrap_result(await self._pbx_execute(path, kwargs))
        except Exception as e:
            raise RuntimeError(
                "".join(traceback.format_exception_only(type(e), e)).strip()
            ) from None

    async def _pbx_execute(self, path, kwargs):
        return await self._pbx_tools[path].run(kwargs)

    @staticmethod
    def _make_async(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            return await asyncio.to_thread(fn, *args, **kwargs)

        return wrapper

    @staticmethod
    def _unwrap_result(result: Any) -> Any:
        structured = getattr(result, "structuredContent", None) or getattr(
            result, "structured_content", None
        )
        if structured is not None:
            if isinstance(structured, dict) and tuple(structured) == ("result",):
                return structured["result"]
            return structured

        content = getattr(result, "content", None) or []
        if not content:
            return None

        text = getattr(content[0], "text", None)
        if text is None:
            return None
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def _pbx_tool_info(self, path):
        tool = self._pbx_get_tool(path)
        if not tool:
            return None
        output = tool.outputSchema
        if output:
            output = {k: v for k, v in output.items() if not k.startswith("x-")}
            if output.get("properties", {}).keys() == {"result"}:
                output = output["properties"]["result"]
            if output == {"additionalProperties": True, "type": "object"}:
                output = {"type": "object"}
        return {
            "description": tool.description,
            "parameters": tool.inputSchema,
            "output": output,
        }

    def _pbx_help(self, path=""):
        index = self._pbx_tool_index or {}

        if path and path in index and not any(k.startswith(f"{path}.") for k in index):
            return self._pbx_tool_info(path)

        tree = {}
        prefix = f"{path}." if path else ""
        for tool_path in index:
            if path and not tool_path.startswith(prefix):
                continue
            relative = tool_path[len(prefix) :] if path else tool_path
            if not relative:
                relative = index[tool_path].leaf
            parts = relative.split(".")
            node = tree
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = None

        if not tree:
            return f"No documentation for '{path}'"

        lines = []
        doc = self.__doc__
        if doc:
            lines.append(f"Description: {doc}")
            lines.append("")
        lines.append("Methods:")
        self._render_tree(tree, lines, "")
        return "\n".join(lines)

    @staticmethod
    def _render_tree(node, lines, prefix):
        items = sorted(node.items(), key=lambda x: (x[1] is not None, x[0]))
        for i, (name, children) in enumerate(items):
            last = i == len(items) - 1
            connector = "└── " if last else "├── "
            if children is None:
                lines.append(f"{prefix}{connector}{name}()")
            else:
                lines.append(f"{prefix}{connector}{name}")
                extension = "    " if last else "│   "
                Bridge._render_tree(children, lines, prefix + extension)

    def _pbx_searchable(self):
        return self._pbx_tool_index or {}

    def _pbx_build_tools(self):
        target = self._pbx_target if self._pbx_target is not None else self
        self._pbx_tools = {}
        self._pbx_tool_index = {}
        self._pbx_crawl(target, "", 0)
        for path, tool in self._pbx_tools.items():
            mcp_tool = tool.to_mcp_tool()
            self._pbx_tool_index[path] = SearchItem.from_schema(
                path, mcp_tool.description, mcp_tool.inputSchema
            )

    def _pbx_crawl(self, obj, path, depth):
        if depth > MAX_NAMESPACE_DEPTH:
            return
        for attr in dir(obj):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(obj, attr, None)
            except Exception:
                continue
            full = f"{path}.{attr}".lstrip(".")
            if callable(val):
                fn = val if inspect.iscoroutinefunction(val) else self._make_async(val)
                self._pbx_tools[full] = FunctionTool.from_function(
                    _encode_bytes_result(fn), name=full.replace(".", "_")
                )
            elif isinstance(val, Bridge):
                self._pbx_crawl(val, full, depth + 1)

    @classmethod
    def wrap(cls, value: Any) -> "Bridge":
        if isinstance(value, Bridge):
            return value
        if callable(value) and not isinstance(value, type):
            return cls._from_callable(value)
        return cls._from_object(value)

    @classmethod
    def _from_callable(cls, fn) -> "Bridge":
        async_fn = fn if inspect.iscoroutinefunction(fn) else cls._make_async(fn)
        tool = FunctionTool.from_function(
            _encode_bytes_result(async_fn), name=fn.__name__
        )
        mcp = tool.to_mcp_tool()
        b = cls()
        b._pbx_type = "function"
        b._pbx_tools = {"": tool}
        b._pbx_tool_index = {
            "": SearchItem.from_schema(fn.__name__, mcp.description, mcp.inputSchema)
        }
        return b

    @classmethod
    def _from_object(cls, obj) -> "Bridge":
        b = cls()
        b._pbx_target = obj
        return b


from parselbox.bridge.graphql import GraphQLBridge
from parselbox.bridge.http import HTTPBridge
from parselbox.bridge.mcp import MCPBridge
from parselbox.bridge.shell import ShellBridge
from parselbox.bridge.toolkit import Toolkit

__all__ = [
    "Bridge",
    "GraphQLBridge",
    "HTTPBridge",
    "MCPBridge",
    "ShellBridge",
    "Toolkit",
]
