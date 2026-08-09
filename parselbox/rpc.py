import asyncio
import json
import secrets
from typing import Any
from collections.abc import Callable, Coroutine

from .logging import logger


class RpcClient:
    def __init__(
        self, proc: asyncio.subprocess.Process, log_handler: Callable | None = None
    ):
        self._proc = proc
        self._pending: dict[str, asyncio.Future] = {}
        self._handlers: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}
        self._ready: asyncio.Event | None = None
        self._log_handler = log_handler
        self._reader_task: asyncio.Task | None = None

    def handle(self, method: str, fn: Callable[..., Coroutine[Any, Any, Any]]):
        self._handlers[method] = fn

    async def call(self, method: str, params: Any = None) -> Any:
        msg_id = secrets.token_hex(8)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        await self._send({"id": msg_id, "method": method, "params": params})
        return await fut

    async def _send(self, msg: dict):
        self._proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self._proc.stdin.drain()

    async def start(self):
        self._ready = asyncio.Event()
        self._reader_task = asyncio.create_task(self._reader())
        await asyncio.wait_for(self._ready.wait(), timeout=30.0)

    async def close(self):
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.kill()
        await self._proc.wait()

    async def _reader(self):
        while True:
            try:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"RPC reader error: {e}")
                break

        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(Exception("Sandbox process terminated"))
        self._pending.clear()

    def _dispatch(self, msg: dict):
        if "result" in msg or "error" in msg:
            fut = self._pending.pop(msg.get("id"), None)
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(Exception(msg["error"]))
                else:
                    fut.set_result(msg.get("result"))
            return

        method = msg.get("method")
        params = msg.get("params", {})
        msg_id = msg.get("id")

        if method == "ready":
            self._ready.set()
        elif method == "log":
            if self._log_handler:
                self._log_handler(
                    params.get("level", "info"),
                    params.get("category", ""),
                    params.get("message", ""),
                )
        elif method in self._handlers:
            asyncio.create_task(self._handle_request(msg_id, method, params))

    async def _handle_request(self, msg_id: str | None, method: str, params: Any):
        try:
            result = await self._handlers[method](params)
            if msg_id is not None:
                await self._send({"id": msg_id, "result": result})
        except ConnectionResetError:
            pass
        except Exception as e:
            if msg_id is not None:
                try:
                    await self._send({"id": msg_id, "error": str(e)})
                except ConnectionResetError:
                    pass
