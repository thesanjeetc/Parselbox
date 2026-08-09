import hashlib
import json
from pathlib import Path

import keyring
from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.client.auth import OAuth
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1KeySanitizationStrategy,
    FileTreeV1CollectionSanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.types import Tool

import re

from parselbox.bridge import Bridge, _current_task
from parselbox.logging import logger
from parselbox.models import SearchItem

PBX_DIR = Path.home() / ".parselbox"


def _get_token_key():
    try:
        key = keyring.get_password("parselbox", "token_key")
        if key:
            return key.encode()
        key = Fernet.generate_key()
        keyring.set_password("parselbox", "token_key", key.decode())
        return key
    except Exception as e:
        logger.debug(f"Keyring unavailable: {e}")
    key_file = PBX_DIR / ".token_key"
    PBX_DIR.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        return key_file.read_bytes()
    key = Fernet.generate_key()
    key_file.write_bytes(key)
    key_file.chmod(0o600)
    return key


def _token_store(name: str):
    token_dir = PBX_DIR / "tokens" / name
    token_dir.mkdir(parents=True, exist_ok=True)
    return FernetEncryptionWrapper(
        key_value=FileTreeStore(
            data_directory=token_dir,
            key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(token_dir),
            collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
                token_dir
            ),
        ),
        fernet=Fernet(_get_token_key()),
    )


def _resolve_auth(auth, url):
    if isinstance(auth, str) and auth == "oauth":
        auth = {}
    if isinstance(auth, dict):
        auth = dict(auth)
        auth["mcp_url"] = auth.pop("url", url)
        discovery_url = auth["mcp_url"]
        store_key = hashlib.sha256(discovery_url.encode()).hexdigest()[:12]
        auth.setdefault("token_storage", _token_store(store_key))
        return OAuth(**auth)
    return auth


class MCPBridge(Bridge):
    _pbx_type = "mcp"

    def __init__(self, config):
        self._active_ctx = None

        client_kwargs = dict(
            log_handler=self._on_log,
            progress_handler=self._on_progress,
        )

        if isinstance(config, Client):
            self.client = (
                Client(
                    config.transport,
                    **client_kwargs,
                )
                if hasattr(config, "transport")
                else config
            )
            self.name = getattr(config, "name", "mcp")
            self.server = None
            self.tools = {}
            return

        self.name = next(iter(config["mcpServers"]))
        self.server = config["mcpServers"][self.name]

        if self.server.get("command"):
            if not self.server.get("env"):
                self.server["env"] = {}
            self.server["env"]["PARSELBOX_MCP_PROBE"] = "true"

        auth = _resolve_auth(self.server.pop("auth", None), self.server.get("url", ""))

        if auth and self.server.get("url"):
            self.client = Client(self.server["url"], auth=auth, **client_kwargs)
        else:
            self.client = Client(config, **client_kwargs)
        self.tools = {}

    async def _on_progress(self, progress, total, message):
        ctx = self._active_ctx
        if not ctx:
            return
        if total is not None:
            pct = int(progress / total * 100)
            ctx.write_log(f"[{int(progress)}/{int(total)}] ({pct}%) {message or ''}")
        else:
            ctx.write_log(message or f"progress: {progress}")

    async def _on_log(self, message):
        ctx = self._active_ctx
        if not ctx:
            return
        data = message.data
        if isinstance(data, dict):
            data = data.get("msg", data.get("message", str(data)))
        ctx.write_log(f"[{message.level}] {data}")

    async def _pbx_connect(self):
        await self.client.__aenter__()
        if self.client.initialize_result.serverInfo.name == "Parselbox":
            return
        await self._refresh_tools()

    async def _refresh_tools(self):
        tools = await self.client.list_tools()
        self.tools = {}
        for tool in tools:
            tool_name = re.sub(r"[^a-zA-Z0-9_]", "_", tool.name).strip("_")
            self.tools[tool_name] = tool
        self._pbx_tools = {}
        self._pbx_tool_index = {}
        for name, tool in self.tools.items():
            self._pbx_tool_index[name] = SearchItem.from_schema(
                name, tool.description, tool.inputSchema or {}
            )

    async def _pbx_close(self):
        await self.client.__aexit__(None, None, None)
        self.tools = {}
        self._pbx_tool_index = None

    @property
    def __doc__(self):
        return (
            f"MCP Server '{self.name}' ({len(self.tools)} tools).\n"
            f"Call tools with keyword arguments: {self.name}.tool_name(param=value)."
        )

    def __dir__(self):
        if not self.client.is_connected():
            return []
        return list(self.tools.keys())

    def _pbx_get_tool(self, path) -> Tool | None:
        return self.tools.get(path)

    async def _pbx_execute(self, path, kwargs):
        self._active_ctx = _current_task.get(None)
        try:
            return await self.client.call_tool(
                name=self.tools[path].name, arguments=kwargs, raise_on_error=True
            )
        finally:
            self._active_ctx = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if not self.client.is_connected():
            raise AttributeError(f"'{self.name}' is not connected")
        tools = object.__getattribute__(self, "tools")
        if name not in tools:
            raise AttributeError(f"Tool '{name}' not found in '{self.name}'.")
        return tools[name]

    @classmethod
    def from_config(cls, mcp) -> dict[str, "MCPBridge"]:
        if not mcp:
            return {}
        if isinstance(mcp, str) and mcp.lstrip().startswith("{"):
            mcp = json.loads(mcp)
        elif isinstance(mcp, (str, Path)):
            mcp = json.loads(Path(mcp).read_text(encoding="utf-8"))
        if "mcpServers" not in mcp:
            raise ValueError("MCP config must contain 'mcpServers' key")
        return {
            name: cls({"mcpServers": {name: config}})
            for name, config in mcp["mcpServers"].items()
        }
