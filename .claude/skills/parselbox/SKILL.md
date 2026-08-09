---
name: parselbox
description: Drive a Parselbox sandbox effectively. Use when a Parselbox execute_code MCP tool is available, when writing code against the parselbox Python SDK, or when wiring parselbox into an MCP config. Covers discovery (sbx.*), stateful execution, background tasks, bash/js/require interop, filesystem, and serve/@api patterns.
---

# Parselbox

Parselbox is a secure Python sandbox (Deno + Pyodide, single process) that exposes external systems — MCP servers, REST/GraphQL APIs, shells, host functions — as **native Python objects** behind one `execute_code` tool. You write Python; tool calls, files, packages, and interop all happen inside it.

## Driving a sandbox (the `execute_code` tool)

Run `sbx.help()` in the sandbox for the complete guide. The essentials:

**Execution semantics**
- **Stateful** — variables, imports, and running tasks persist across calls. Build up state; don't re-do work.
- The **last expression** is the result; it must be JSON-serializable (`bytes` are fine — they cross as base64 under the hood; anything else falls back to `repr()`).
- `await` works at top level. Use `await asyncio.sleep()`, never `time.sleep()`.
- `print()` is not shown to MCP clients — **return values instead** (SDK callers get it via `result.stdout`).
- New/modified files are detected automatically and returned with the result.

**Discover before you call**
```python
sbx.info()                        # what's wired in: namespaces, network, mounts, files
sbx.search("invoice|create")      # regex across every namespace's names/descriptions/params
sbx.inspect(["tracker.create_issue", "store.get"])   # exact schemas, only when about to call
sbx.preview(big_response)         # truncated view of unknown/large structures
```
Skip discovery when the namespace matches tools you already know. MCP tools require **keyword arguments**.

**Background tasks** — append `.task()` to any bridge call:
```python
job = vm.exec.task(command="ffmpeg -i in.mp4 out.mp4")
job.status()                   # state, elapsed, last log line, logfile path
job.tail(5)                    # read its live log
await job.wait(timeout=120)    # or: await job
job.cancel()

results = await asyncio.gather(*[api.get.task(f"/items/{i}") for i in range(5)])  # parallel fan-out
```
Interactive sessions: `session = sh.shell.task()` keeps stdin open — `session.send("cmd")`, then `session.tail()` after a beat. An optional first command launches a REPL as the session.

**Polyglot**
```python
bash("grep -rn TODO . | wc -l")           # pure-JS bash: pipes, coreutils, curl. Each call isolated.
js("return items.filter(fn)", items=[1,2,3], fn=lambda x, *_: x > 1)   # callbacks auto-proxied
lodash = require("lodash")                 # npm; also local ./mod.ts (hot-reload) and ./mod.wasm
color = require("color")
color("red").darken(0.5).hex()            # chains + auto-`new` constructors work from Python
pandoc = require("./pandoc.wasm")         # WASI command binaries become callables — and bash() commands
```

**Files** — use relative paths for outputs (they persist and get reported). Inputs live at `/files/`, host mounts at `/mnt/<name>` (check `sbx.info()` for `ro`/`rw`).

**Big outputs** — oversized results are auto-truncated. Offload instead: write to disk, then `bash("grep ... file | head")` to pull only what you need.

**Web server** (when `serve` is enabled): files in the working dir are served at `/`; register live endpoints with `@api.get("/path")` / `@api.post(...)` (handlers can call any bridge); `POST /_upload` accepts files; static changes live-reload the browser.

**Inline views** — in MCP Apps hosts (Claude Desktop, VS Code), `display(html_or_path)` renders HTML as a widget beneath the result (Tailwind/daisyUI auto-injected); `pbx.call("/api/route", body)` inside the view reaches `@api` handlers. One view per execution — last call wins.

## Setting up Parselbox (SDK / CLI)

Requires Deno (`curl -fsSL https://deno.land/install.sh | sh`) and `pip install parselbox`.

**SDK**
```python
from parselbox import Parselbox
from parselbox.bridge import HTTPBridge, GraphQLBridge, ShellBridge

async with Parselbox(
    mcp={"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}},
    context={"api": HTTPBridge(base_url="https://api.github.com", token=...), "sh": ShellBridge("bash")},
    network=True,                  # False (default), True, or ["domain:port", ...]
    allow_runtime_packages=True,   # auto-install imports
    output_dir="./workspace",      # persist sandbox files
    serve=3000,                    # optional web server
    timeout=60,
) as sbx:
    result = await sbx.execute_code("sbx.info()")
    await sbx.run_mcp()            # expose as an MCP server (stdio; or transport="http", port=9000)
```

**CLI / MCP config** — the "loopback" trick: point `--mcp` at the same config file so other servers' tools appear inside the sandbox (Parselbox skips connecting to itself):
```json
{
  "mcpServers": {
    "github": {},
    "parselbox": {"command": "uvx", "args": ["parselbox", "--mcp", "/absolute/path/to/mcp.json"]}
  }
}
```
Remote MCP servers that need login take `"auth": "oauth"` (browser flow once; tokens cached encrypted).

Full reference (all config, filesystem, security, proxy patterns): the repo README — every chapter ends with a runnable example in `examples/parselbox-basics/`.
