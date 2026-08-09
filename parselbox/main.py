import asyncio
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .logging import logger
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from . import view
from .bridge import Bridge, MCPBridge, Toolkit, TaskContext, _current_task
from .context import ContextManager
from .hooks import Hook
from .mcp import ParselboxMCP
from .models import Callback, ExecutionResult, Mount, SandboxError
from .rpc import RpcClient

from .prompt import (
    PARSELBOX_PROMPT,
    PARSELBOX_SERVE_PROMPT,
    PARSELBOX_UI_PROMPT,
)

DENO_SANDBOX_DIR = Path(__file__).parent.resolve() / "sandbox"
DENO_SCRIPT_PATH = str(DENO_SANDBOX_DIR / "main.ts")
DENO_CACHE_DIR = str(Path.home() / ".cache" / "parselbox" / "deno")


class Parselbox:
    def __init__(
        self,
        globals: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        mcp: str | dict | None = None,
        files: list[str] | None = None,
        mounts: list[Mount] | None = None,
        output_dir: str | None = None,
        network: bool | list[str] = False,
        packages: list[str] | None = None,
        package_dir: str | None = None,
        allow_runtime_packages: bool = False,
        memory: int = 2048,
        timeout: int = 60,
        serve: int | None = None,
        hooks: list[Hook] | None = None,
        env: dict[str, str] | None = None,
    ):
        if os.environ.get("PARSELBOX_MCP_PROBE"):
            sys.exit(0)

        self.deno_path = self._check_deno_exists()

        self.mounts = []
        for m in mounts or []:
            if Path(m.host).exists():
                self.mounts.append(m)
            else:
                logger.warning(f"Mount folder does not exist, skipping: {m.target}")
        self.skills_dir = next(
            (m.host for m in self.mounts if m.target.strip("/") == "skills"), None
        )
        self.files = [str(Path(f).resolve()) for f in (files or [])]
        self.cache_dir = tempfile.TemporaryDirectory()
        self.output_dir = (
            str(Path(output_dir).resolve())
            if output_dir
            else str(Path(self.cache_dir.name) / "workspace")
        )
        self.tasks_dir = str(Path(self.output_dir) / ".parselbox" / "tasks")
        self.tmp_dir = str(Path(self.cache_dir.name) / "tmp")
        self.package_dir = (
            str(Path(package_dir).resolve())
            if package_dir
            else str(Path(self.cache_dir.name) / "packages")
        )
        self.files_dir = str(Path(self.cache_dir.name) / "files")
        for d in [
            self.output_dir,
            self.tasks_dir,
            self.tmp_dir,
            self.files_dir,
            self.package_dir,
            os.path.join(self.package_dir, "site-packages"),
            DENO_CACHE_DIR,
        ]:
            os.makedirs(d, exist_ok=True)

        self._deno_config = self._setup_packages(packages or [])

        self.globals = globals
        self.toolkit = Toolkit()
        merged = (context or {}) | MCPBridge.from_config(mcp) | {"sbx": self.toolkit}
        self.context = {}
        for k, v in merged.items():
            self.context[re.sub(r"[^a-zA-Z0-9_]", "_", k).strip("_")] = Bridge.wrap(v)
        self.executor = ContextManager(self.context)

        self.allow_runtime_packages = allow_runtime_packages
        self.memory = memory
        self.timeout = timeout
        self.network = network
        self.serve = serve

        self.packages = packages or []
        self.hooks = hooks or []
        self.env = env or {}
        self.ui = False

        self._rpc: RpcClient | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._exec_lock = asyncio.Lock()
        self._send_queues: dict[str, asyncio.Queue] = {}
        self._pending_view: str | None = None
        self.parselbox_mcp = ParselboxMCP(sandbox=self)

    def _check_deno_exists(self):
        candidates = []
        bin_name = "deno.exe" if platform.system() == "Windows" else "deno"
        if which := shutil.which("deno"):
            candidates.append(Path(which))
        candidates.append(Path.home() / ".deno" / "bin" / bin_name)
        if platform.system() == "Windows" and "LOCALAPPDATA" in os.environ:
            candidates.append(
                Path(os.environ["LOCALAPPDATA"]) / "deno" / "bin" / bin_name
            )
        found = next((p for p in candidates if p.exists() and p.is_file()), None)
        if not found:
            raise RuntimeError("Deno not found on PATH or default locations.")
        deno_path = str(found)
        try:
            subprocess.run([deno_path, "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise RuntimeError(f"Deno not found or failed to run: {e}")
        return deno_path

    def _setup_packages(self, packages: list[str]) -> str:
        config_dir = Path(self.cache_dir.name) / "deno"
        config_dir.mkdir(exist_ok=True)
        for f in ["deno.jsonc", "deno.lock"]:
            shutil.copy2(DENO_SANDBOX_DIR / f, config_dir / f)
        config = str(config_dir / "deno.jsonc")

        npm_packages = [p for p in packages if p.startswith("npm:")]
        if npm_packages:
            env = {"DENO_DIR": DENO_CACHE_DIR, "DENO_NO_PACKAGE_JSON": "1"}
            cmd = [self.deno_path, "cache", f"--config={config}", *npm_packages]
            subprocess.run(cmd, env=env, capture_output=True)

        return config

    def _build_deno_args(self) -> list[str]:
        args = ["run"]
        args.append(f"--config={self._deno_config}")
        args.append("--node-modules-dir=false")
        if not self.allow_runtime_packages:
            args.append("--frozen")

        read_write_paths = [
            DENO_CACHE_DIR,
            self.package_dir,
        ]

        if self.output_dir:
            read_write_paths.append(self.output_dir)

        read_write_paths.append(self.files_dir)
        read_write_paths.append(self.tmp_dir)
        read_only_paths = [str(DENO_SANDBOX_DIR)]

        for m in self.mounts:
            if m.mode == "rw":
                read_write_paths.append(m.host)
            else:
                read_only_paths.append(m.host)

        all_readable = read_only_paths + read_write_paths

        args.append(f"--allow-read={','.join(sorted(set(all_readable)))}")
        args.append(f"--allow-write={','.join(sorted(set(read_write_paths)))}")

        if self.network is True:
            args.append("--allow-net")
        else:
            allowed_domains = set()
            if self.packages or self.allow_runtime_packages:
                allowed_domains.update(
                    {
                        "cdn.jsdelivr.net:443",
                        "pypi.org:443",
                        "files.pythonhosted.org:443",
                        "registry.npmjs.org:443",
                    }
                )
            if isinstance(self.network, list):
                allowed_domains.update(self.network)
            if self.serve:
                allowed_domains.add(f"0.0.0.0:{self.serve}")
                allowed_domains.add(f"localhost:{self.serve}")
            if allowed_domains:
                args.append(f"--allow-net={','.join(sorted(allowed_domains))}")
            else:
                args.append("--deny-net")

        args.append(
            f"--v8-flags=--optimize-for-size"
            f",--wasm-num-compilation-tasks=2"
            f",--max-heap-size={self.memory}"
        )
        args.append("--allow-env")
        args.append("--deny-sys")
        args.append("--deny-run")
        args.append("--deny-ffi")

        args.append(DENO_SCRIPT_PATH)
        return args

    def _handle_log(self, level, category, message):
        if category not in ("deno", "pyodide") or level not in (
            "debug",
            "info",
            "warning",
            "error",
        ):
            return
        getattr(logger, level)(message, extra={"component": category})

    def is_connected(self):
        return (
            self._rpc is not None
            and self._proc is not None
            and self._proc.returncode is None
        )

    def _build_config(self) -> dict:
        mounts = {m.host: m.target for m in self.mounts}
        allow_net = bool(self.network) or self.allow_runtime_packages or self.serve
        payload = {
            "globals": self.globals,
            "mounts": mounts,
            "files_dir": self.files_dir,
            "output_dir": self.output_dir,
            "tmp_dir": self.tmp_dir,
            "context": list(self.context.keys()),
            "packages": self.packages,
            "disable_net": not allow_net,
            "allow_runtime_packages": self.allow_runtime_packages,
            "package_dir": self.package_dir,
            "memory": self.memory,
            "timeout": self.timeout,
            "serve": self.serve,
        }
        return {k: v for k, v in payload.items() if v is not None}

    async def _start_sandbox(self):
        deno_args = self._build_deno_args()
        config = self._build_config()
        env = {
            **self.env,
            "DENO_DIR": DENO_CACHE_DIR,
            "PARSELBOX_CONFIG": json.dumps(config),
            "MALLOC_ARENA_MAX": "1",
            "DENO_NO_PACKAGE_JSON": "1",
        }
        self._proc = await asyncio.create_subprocess_exec(
            self.deno_path,
            *deno_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=50 * 1024 * 1024,
            cwd=tempfile.gettempdir(),
            env=env,
        )
        self._rpc = RpcClient(self._proc, log_handler=self._handle_log)
        self._rpc.handle("callback", self._handle_callback)
        await self._rpc.start()

    async def connect(self, bridge_timeout: int = 30):
        if self.is_connected():
            return

        logger.info("Starting sandbox...")
        bridges = {k: b for k, b in self.context.items() if isinstance(b, Bridge)}
        try:
            results = await asyncio.gather(
                self._start_sandbox(),
                *(
                    asyncio.wait_for(b._pbx_connect(), timeout=bridge_timeout)
                    for b in bridges.values()
                ),
                return_exceptions=True,
            )
            sandbox_result = results[0]
            if isinstance(sandbox_result, Exception):
                raise sandbox_result
            for name, result in zip(bridges.keys(), results[1:]):
                if isinstance(result, asyncio.TimeoutError):
                    logger.error(f"Bridge '{name}' timed out after {bridge_timeout}s")
                elif isinstance(result, Exception):
                    logger.error(f"Failed to connect bridge '{name}': {result}")
        except Exception as e:
            await self.close()
            raise RuntimeError(f"Failed to connect to sandbox: {e}") from e

        for b in self.context.values():
            if isinstance(b, Bridge):
                b._pbx_sandbox = self
        self.toolkit._build_index()
        await self.upload_files(self.files)

    async def close(self):
        if not self.is_connected():
            return
        logger.debug("Closing sandbox...")
        await self._cleanup()
        logger.debug("Sandbox closed")

    async def _cleanup(self, keep_cache=False):
        tasks = []
        if self._rpc:
            tasks.append(self._rpc.close())
        if not keep_cache:
            tasks.extend(
                [v._pbx_close() for v in self.context.values() if isinstance(v, Bridge)]
            )
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if not keep_cache:
            self.cache_dir.cleanup()
        self._send_queues.clear()
        if os.path.exists(self.tasks_dir):
            shutil.rmtree(self.tasks_dir)
            os.makedirs(self.tasks_dir)
        self._rpc = None
        self._proc = None

    async def _reconnect(self):
        await self._cleanup(keep_cache=True)
        await self.connect()

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb):
        await self.close()

    async def _run_hooks(self, method, *args):
        for hook in self.hooks:
            await getattr(hook, method)(*args)

    async def _handle_callback(self, params: dict) -> Any:
        callback = Callback(**params)

        if callback.op == "_task_send":
            task_id = callback.args[0] if callback.args else None
            if task_id and task_id in self._send_queues:
                self._send_queues[task_id].put_nowait(callback.args[1])
            return

        if callback.op == "_display":
            self._pending_view = view.render(
                callback.kwargs.get("html", ""), serve=self.serve
            )
            return None

        logger.debug(f"Function Call: {callback}")
        try:
            await self._run_hooks("pre_tool_call", callback)
        except Exception as e:
            return {"__error__": str(e), "__error_type__": type(e).__name__}

        task = asyncio.create_task(self._execute_with_context(callback))
        return await task

    async def _execute_with_context(self, callback) -> Any:
        task_id = callback.task_id
        if task_id:
            task_id = Path(task_id).name or "task"
            queue = self._send_queues.setdefault(task_id, asyncio.Queue())
            ctx = TaskContext(
                task_id=task_id,
                log_path=Path(self.tasks_dir) / f"{task_id}.log",
                queue=queue,
                started_at=time.time(),
            )
            _current_task.set(ctx)

        try:
            data = await self.executor.execute(callback)
        except Exception as e:
            return {"__error__": str(e), "__error_type__": type(e).__name__}
        await self._run_hooks("post_tool_call", callback, data)
        return data

    async def _call_rpc(self, method, params):
        if not self.is_connected():
            await self.connect()
        try:
            return await self._rpc.call(method, params)
        except Exception as e:
            logger.error(f"Sandbox process crashed: {e}")
            try:
                await self._reconnect()
            except Exception as reconnect_err:
                logger.debug(f"Reconnect failed: {reconnect_err}")
            raise SandboxError(
                "Sandbox process restarted. All state has been lost. Please try again."
            ) from e

    async def upload_files(self, files: list[str]):
        if not files:
            return
        logger.debug(f"Copying files: {', '.join([Path(f).name for f in files])}")

        def _copy_sync(path: str):
            target_path = Path(path).resolve()
            if not target_path.is_file():
                return

            mount_path = Path(self.files_dir) / target_path.name

            if mount_path.exists():
                mount_path.unlink()

            shutil.copy2(target_path, mount_path)

        loop = asyncio.get_running_loop()
        tasks = [loop.run_in_executor(None, _copy_sync, f) for f in files]
        await asyncio.gather(*tasks)

    def resolve_path(self, sandbox_path: str) -> Path:
        p = sandbox_path
        if not p.startswith("/"):
            p = f"/workspace/{p}"
        p = os.path.normpath(p)
        mapping = [
            ("/workspace", self.output_dir),
            ("/files", self.files_dir),
            ("/tmp", self.tmp_dir),
        ]
        for m in self.mounts:
            mapping.append((f"/mnt/{m.target.strip('/')}", m.host))
        for prefix, host_dir in sorted(mapping, key=lambda x: -len(x[0])):
            if p == prefix or p.startswith(prefix + "/"):
                rel = p[len(prefix) :].lstrip("/")
                return Path(host_dir) / rel if rel else Path(host_dir)
        raise SandboxError(f"Path not backed by disk: {sandbox_path}")

    def read_file(self, path: str) -> bytes | str:
        host_path = self.resolve_path(path)
        if not host_path.exists():
            raise SandboxError(f"File not found: {path}")
        if host_path.is_dir():
            raise SandboxError(f"Path is a directory: {path}")
        data = host_path.read_bytes()
        if b"\x00" in data:
            return data
        try:
            return data.decode()
        except (UnicodeDecodeError, ValueError):
            return data

    def write_file(self, path: str, content: bytes | str) -> None:
        host_path = self.resolve_path(path)
        host_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            host_path.write_text(content)
        else:
            host_path.write_bytes(content)

    _console = Console(stderr=True)

    def _print_syntax(
        self, code: str, lexer: str = "python", line_numbers: bool = False
    ):
        self._console.print()
        self._console.print(
            Syntax(
                code,
                lexer,
                theme="monokai",
                line_numbers=line_numbers,
                background_color="default",
                word_wrap=True,
            )
        )
        self._console.print()

    async def execute_code(self, code: str) -> ExecutionResult:
        async with self._exec_lock:
            logger.info("Executing code:")
            self._print_syntax(code, "python", line_numbers=True)
            self._pending_view = None

            try:
                await self._run_hooks("pre_execute", code)
            except Exception as e:
                logger.warning(f"Blocked by hook: {e}")
                return ExecutionResult(is_success=False, error=f"Blocked by hook: {e}")

            try:
                deadline = (self.timeout + 5) if self.timeout else None
                coro = self._call_rpc("exec", {"code": code})
                response = await (
                    asyncio.wait_for(coro, timeout=deadline) if deadline else coro
                )
            except asyncio.TimeoutError:
                logger.error("Execution timed out, restarting...")
                await self._reconnect()
                return ExecutionResult(
                    is_success=False,
                    error=f"Execution timed out after {self.timeout}s. Sandbox process restarted. All state has been lost. Please try again.",
                )
            except SandboxError as e:
                return ExecutionResult(is_success=False, error=str(e))
            result = ExecutionResult(**response)
            result.view = self._pending_view
            self._pending_view = None

            if result.error:
                logger.error("Execution failed:")
                self._print_syntax(result.error, "pytb")

            if result.output is not None:
                output_str = str(result.output)
                if len(output_str) > 500:
                    output_str = output_str[:500] + "..."
                logger.info("Execution output:")
                self._console.print(Panel(output_str, border_style="dim"))
            elif not result.error:
                logger.info("Execution completed")

            if result.files:
                names = [f.rsplit("/", 1)[-1] for f in result.files]
                if len(names) > 5:
                    logger.info(
                        f"Files: {', '.join(names[:5])} (+{len(names) - 5} more)"
                    )
                elif names:
                    logger.info(f"Files: {', '.join(names)}")

            await self._run_hooks("post_execute", result)
        return result

    async def run_mcp(
        self, transport="stdio", host="0.0.0.0", port=9000, elicit=False, ui=True
    ):
        if elicit:
            self.parselbox_mcp.enable_elicit()
        self.parselbox_mcp.set_ui(ui)
        await self.parselbox_mcp.run(transport=transport, host=host, port=port)

    def get_tool(self):
        return self.parselbox_mcp.execute_code

    def get_prompt(self, include_serve: bool = True, include_ui: bool = True) -> str:
        parts = [PARSELBOX_PROMPT]

        if include_ui and self.ui:
            parts.append(PARSELBOX_UI_PROMPT)

        if include_serve and self.serve:
            parts.append(PARSELBOX_SERVE_PROMPT)

        return "".join(parts)
