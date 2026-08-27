<!-- mcp-name: io.github.thesanjeetc/parselbox -->
![Parselbox SDK](https://github.com/thesanjeetc/parselbox/blob/main/assets/parselbox-dark.png#gh-dark-mode-only)
![Parselbox SDK](https://github.com/thesanjeetc/parselbox/blob/main/assets/parselbox-light.png#gh-light-mode-only)

<div align="center">

>***Code. Filesystem. Context. Tools.***<br/>
>**What if agents had one tool to rule them all?**

</div>

<h4 align="center">
  <a href="https://github.com/thesanjeetc/parselbox/blob/main/LICENSE.md">
    <img alt="License" src="https://img.shields.io/badge/license-MIT-blue.svg?style=for-the-badge">
  </a>
  <a href="https://pypi.org/project/parselbox/">
    <img alt="PyPI - Version" src="https://img.shields.io/pypi/v/parselbox?style=for-the-badge">
  </a>
  <a href="https://github.com/thesanjeetc/parselbox/actions/workflows/ci.yaml">
    <img alt="CI" src="https://img.shields.io/github/actions/workflow/status/thesanjeetc/parselbox/ci.yaml?branch=main&style=for-the-badge&label=CI">
  </a>
</h4>

Parselbox is an embeddable Python runtime where AI agents call tools as code — MCP servers, APIs, and shells become native Python objects. Disk-backed workspace, packages, and networking built in; a single-process execution layer powered by [Deno](https://deno.com/) and [Pyodide](https://pyodide.org/en/stable/).

https://github.com/user-attachments/assets/d4e43d16-3aa3-4e29-83c7-a1d3885b8045

> [!TIP]
> Drop the [Parselbox MCP](#parselbox-mcp) alongside existing MCP server configurations. Agents instantly get a Python runtime, MCP tools as code, support for skills and a disk-backed workspace.

## Features

#### 🔒 Secure Isolation
No containers, no VMs — just a single, lightweight Deno + Pyodide process (~160 MB). Deno permissions, memory caps, timeouts, network allowlists. Snapshot caching and crash recovery.

#### 🛠️ Tools as Code
MCP servers, REST + OpenAPI, GraphQL, shell, functions and classes — all native Python objects. Stateful across calls. Pydantic auto-conversion. Credentials stay on the host.

#### 🐍 Polyglot Runtime
Full CPython with `js()` interop — use JS packages as native Python. `require()` for npm, local TypeScript, and `.wasm` modules. Virtual `bash()` for shell. Auto-install packages on import.

#### 📦 WASM Tools
`require()` any `.wasm` — library exports become Python methods, WASI programs become callable commands; drop one in `bin/` to run it from `bash()` too. In-process, inherits the sandbox's mounts and permissions, installs nothing on the host.

#### ⚡ Background Tasks
Append `.task()` to any call — parallel fan-out with `asyncio.gather`, check progress, tail logs, drive interactive sessions with `send()`, await later.

#### 📁 Filesystem Integration
Disk-backed workspace — host mounts (`ro`/`rw`), input files at `/files/`, outputs persisted to real directories. New and modified files are detected and returned per call.

#### 🔍 Progressive Disclosure
`help()`, `search()`, `inspect()`, `preview()` — agents discover only what they need, when they need it.

#### 🎨 Generative UI
`display()` renders HTML inline in the chat (MCP Apps), with Tailwind + daisyUI injected. Or serve a full app — built-in HTTP server with static files, live reload, file upload, and `@api` routes that compose across tools.

---

## Contents

- [Quick Start](#quick-start)
  - [Parselbox API](#parselbox-api)
  - [Parselbox MCP](#parselbox-mcp)
  - [Parselbox Agents](#parselbox-agents)
- [User Guide](#user-guide)
  - [Tools as Code](#1-tools-as-code)
  - [Background Tasks](#2-background-tasks)
  - [Filesystem Integration](#3-filesystem-integration)
  - [Packages & Networking](#4-packages--networking)
  - [JavaScript Interop](#5-javascript-interop)
  - [WASM Tools](#6-wasm-tools)
  - [Progressive Disclosure](#7-progressive-disclosure)
  - [Generative UI](#8-generative-ui)
  - [Sandbox Hooks](#9-sandbox-hooks)
- [Configuration Reference](#configuration-reference)
- [Architecture](#architecture)
- [Security](#security)
- [Related Work](#related-work)

## Quick Start

Parselbox uses [**Deno**](https://deno.com) for the secure sandbox runtime.

**1. Install Deno**

```bash
# macOS / Linux
curl -fsSL https://deno.land/install.sh | sh

# Windows (PowerShell)
irm https://deno.land/install.ps1 | iex
```

**2. Install Parselbox**

```bash
pip install parselbox
```

### Parselbox API

Wire any tool into the sandbox — MCP servers, REST/GraphQL, shells, host objects — and the agent calls them as native Python, composing them with real control flow over a disk-backed workspace and both the Python and npm package ecosystems.

**Example:**

```python
import asyncio
import os
from textwrap import dedent
from parselbox import Parselbox
from parselbox.bridge import HTTPBridge, ShellBridge

class Analytics:
    def summarize(self, repos: list) -> dict:
        """Aggregate repo stats."""
        stars = [r["stars"] for r in repos]
        return {"count": len(repos), "avg_stars": round(sum(stars) / len(stars))}

config = {"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}}

async def main():
    async with Parselbox(
        mcp=config,
        context={
            "analytics": Analytics(),
            "github": HTTPBridge(base_url="https://api.github.com", token=os.environ["GITHUB_TOKEN"]),
            "sh": ShellBridge("bash"),
        },
        network=True,
        allow_runtime_packages=True,
        packages=["numpy", "npm:lodash"],
        output_dir="./workspace",
    ) as sbx:
        # Discover available tools
        await sbx.execute_code("sbx.search('navigate|get')")

        # Scrape Hacker News for GitHub links in a real browser
        await sbx.execute_code(dedent("""
            import re
            playwright.browser_navigate(url="https://news.ycombinator.com")
            text = playwright.browser_snapshot()
            repos = re.findall(r'github\\.com/([\\w.-]+/[\\w.-]+)', text)[:5]
        """))

        # Fetch star counts in parallel, then summarize via the context bridge
        await sbx.execute_code(dedent("""
            import asyncio
            results = await asyncio.gather(*[github.get.task(f"/repos/{r}") for r in repos])
            repo_data = [{"name": r["data"]["name"], "stars": r["data"]["stargazers_count"]}
                         for r in results if r.get("ok")]
            analytics.summarize(repo_data)
        """))

        # Chart it — matplotlib auto-installs on import
        result = await sbx.execute_code(dedent("""
            import matplotlib.pyplot as plt
            plt.barh([r["name"] for r in repo_data], [r["stars"] for r in repo_data])
            plt.savefig("chart.png")
        """))
        print(result.files)                  # ['chart.png']
        image = sbx.read_file("chart.png")
        # every result carries .output, .files, .stdout, .stderr, .error

        # Serve the whole sandbox as an MCP server
        await sbx.run_mcp()

asyncio.run(main())
```

### Parselbox MCP

The Parselbox CLI runs a standalone MCP server — every sandbox option is available as a flag.

#### STDIO

> [!TIP]
> **The "loopback" trick:**
> 1. Add the Parselbox MCP alongside your existing MCP servers.
> 2. Point `--mcp` at that same config file.
> 3. On startup, Parselbox connects to the other servers, exposes their tools inside the sandbox, and starts its own MCP server.
>
> Don't worry — Parselbox detects and avoids connecting to itself. No infinite loops of doom.

**Example:**

```json
{
  "mcpServers": {
    "github": {},
    "linear": {},
    "parselbox": {
      "command": "uvx",
      "args": ["parselbox", "--mcp", "/absolute/path/to/mcp.json"]
    }
  }
}
```

#### HTTP

```bash
uvx parselbox --mcp mcp.json --transport http --port 9000
```

```json
{
  "mcpServers": {
    "parselbox": {
      "type": "http",
      "url": "http://localhost:9000/mcp"
    }
  }
}
```

#### Full Example

```bash
uvx parselbox \
  --mcp ./mcp.json \
  --transport http \
  --host 0.0.0.0 \
  --port 8080 \
  --file hello.txt \
  --mount ./datasets:/data:rw \
  --output-dir ./outputs \
  --packages pandas,matplotlib \
  --package-dir ./cache \
  --allow-runtime-packages \
  --network \
  --serve 3000 \
  --memory 2048 \
  --timeout 60 \
  --env MY_API_KEY=...
```

---

### Parselbox Agents

```python
import asyncio
from parselbox import Parselbox
from agents import Agent, Runner, function_tool

sandbox = Parselbox(
    mcp={"mcpServers": {"playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}}},
    output_dir="./outputs",
    allow_runtime_packages=True,
)

agent = Agent(
    name="Research Assistant",
    model="gpt-5.5",
    instructions=f"You are a world-class research assistant.\n\n{sandbox.get_prompt()}",
    tools=[function_tool(sandbox.get_tool())],
)

async def main():
    async with sandbox:
        result = await Runner.run(
            agent,
            "Scrape Wikipedia's 'List of highest-grossing films' with the Playwright MCP. "
            "Plot a bar chart of the top 10 and save it as ./plot.png",
            max_turns=30,
        )
        print(result.final_output)

asyncio.run(main())
```

## User Guide

### 1\. Tools as Code

The context bridge exposes host Python objects inside the sandbox:

- `context` — functions and namespaces as callable tools. Execution pauses, runs on host, returns result.
- `globals` — static values (strings, numbers, dicts) copied into the sandbox.
- `mcp` — MCP server config (dict or path). Appears as callable namespaces inside sandbox.

**Plain classes** are auto-wrapped — every public method becomes a callable tool; methods starting with `_` stay private:

```python
from parselbox import Parselbox

class Calculator:
    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        return a + b

async with Parselbox(context={"calc": Calculator()}) as sbx:
    await sbx.execute_code("calc.add(a=10, b=20)")
```

Subclass **`Bridge`** for nested namespaces (auto-crawled); annotate a parameter with a Pydantic model and passed dicts convert to it automatically:

```python
from parselbox import Parselbox
from parselbox.bridge import Bridge
from pydantic import BaseModel

class Coordinate(BaseModel):
    x: float
    y: float
    z: float = 0.0

class Sensors(Bridge):
    def temperature(self) -> float:
        """Read temperature in celsius."""
        return 23.5

class Robot(Bridge):
    def __init__(self):
        self.sensors = Sensors()

    def move(self, to: Coordinate) -> dict:
        """Move robot to a position."""
        return {"position": [to.x, to.y, to.z], "status": "reached"}

async with Parselbox(context={"robot": Robot()}) as sbx:
    await sbx.execute_code("robot.move(to={'x': 1, 'y': 2})")
    await sbx.execute_code("robot.sensors.temperature()")
```

Parselbox ships **bridges** for REST, GraphQL, and shell:

```python
from parselbox import Parselbox
from parselbox.bridge import HTTPBridge, GraphQLBridge, ShellBridge

api = HTTPBridge(
    spec="https://petstore3.swagger.io/api/v3/openapi.json",
    base_url="https://petstore3.swagger.io/api/v3",
)
gql = GraphQLBridge("https://countries.trevorblades.com/graphql")
sh = ShellBridge("ssh -T user@host")

mcp = {"mcpServers": {"deepwiki": {"type": "http", "url": "https://mcp.deepwiki.com/mcp"}}}

async with Parselbox(context={"api": api, "gql": gql, "sh": sh}, mcp=mcp, network=True) as sbx:
    await sbx.execute_code('api.search("GET /pet/*")')
    await sbx.execute_code('api.get("/pet/1")')

    await sbx.execute_code('gql.graphql(query="{ continents { name } }")')
    await sbx.execute_code('gql.graphql(query="{ languages { code name } }")')

    await sbx.execute_code('term = sh.shell.task()')
    await sbx.execute_code('term.send("df -h")')

    await sbx.execute_code("sbx.search('ask|read')")
    await sbx.execute_code("deepwiki.read_wiki_structure(repoName='pyodide/pyodide')")
    await sbx.execute_code("deepwiki.ask_question(question='What is Pyodide?', repoName='pyodide/pyodide')")
```

> **Runnable:** [bridges.py](examples/parselbox-basics/bridges.py)

### 2\. Background Tasks

Every context and MCP call also has a `.task()` form that runs on the host without blocking the sandbox — for parallel fan-out, long-running jobs, and interactive sessions:

```python
job = sh.exec.task(command="ffmpeg -i in.mp4 out.mp4")   # returns a task immediately

job.status()                   # TaskStatus(state, elapsed, message, logfile)
job.tail(5)                    # last lines of the task's live log
job.send("q")                  # message a running interactive process
await job.wait(timeout=120)    # block until done — or just `await job`
job.cancel()

# parallel fan-out
import asyncio
results = await asyncio.gather(*[api.get.task(f"/items/{i}") for i in range(5)])
```

MCP tools stream their progress and log notifications into the task's logfile. A custom `Bridge` method emits the same way with `self.log()`, and reads whatever the sandbox queued via `send()` with `self.recv()`:

```python
from parselbox.bridge import Bridge

class Exporter(Bridge):
    def run(self, rows: int) -> str:
        for i in range(rows):
            self.log(f"row {i}/{rows}")     # appended to task.logfile → tail()
            for msg in self.recv():         # messages queued by task.send()
                self.log(f"got: {msg}")
        return "done"
```

**Interactive sessions** — `ShellBridge.shell()` keeps stdin open, so a task can drive a live process with `send()`:

```python
session = sh.shell.task()               # a live shell — state persists within the session
session.send("x=21")
session.send("echo $((x * 2))")

import asyncio
await asyncio.sleep(1)                  # give it a beat
session.tail(1)                         # "42"

session.cancel()
```

An optional first command launches any REPL as the session — e.g. `sh.shell.task("python3 -i")`.

> **Runnable:** [tasks.py](examples/parselbox-basics/tasks.py)

### 3\. Filesystem Integration

Parselbox runs on Pyodide's virtual filesystem, with the working directory, input files, mounts, and packages backed by real host directories — access gated by Deno's permission controls at startup.

| Method         | Access Level   | Description                                                                       |
| :------------- | :------------- | :-------------------------------------------------------------------------------- |
| **files**      | Read / Write   | Temp directory at `/files/`. Input files copied here; server uploads stored here. |
| **mounts**     | Configurable   | Maps host directories to `/mnt/{name}`. Mode: `ro` (default) or `rw`.             |
| **output_dir** | Read / Write   | Maps working directory to a host directory to persist files. If not provided, defaults to a temp directory (wiped on close). |

> [!NOTE]
> - `/workspace` is always backed by a real host directory — `output_dir` (persistent) or an ephemeral temp dir (wiped on close) — enabling Deno streaming, `resolvePath()`, and `require()` for local modules.
> - Cross the boundary with `sandbox.read_file(path)` (`str` for text, `bytes` for binary) and `sandbox.write_file(path, content)`; a persistent `output_dir` is also readable directly.
> - Mounts with `target="skills"` are reported by `sbx.info()` and discoverable via `bash("ls /mnt/skills/")`.


**Example:**

```python
from parselbox import Parselbox, Mount

async with Parselbox(
    files=["data.csv"],                         # Read/write at /files/data.csv
    mounts=[
        Mount("./datasets", "/data", "ro"),     # Read-only at /mnt/data
        Mount("./workspace", "/work", "rw"),    # Read/write at /mnt/work
    ],
    output_dir="./outputs"                      # Sandbox files persisted here
) as sandbox:
    # Write a file into the sandbox from the host
    sandbox.write_file("greeting.txt", "Hello from host!")

    code = """
    content = open('/files/data.csv').read()               # input file
    ref = open('/mnt/data/reference.json').read()          # read-only mount
    open('/mnt/work/processed.txt', 'w').write(content)    # read/write mount
    open('result.txt', 'w').write("Done!")                 # working dir -> output_dir
    """
    result = await sandbox.execute_code(code)

    # New / modified files are detected and returned
    print(result.files)   # ['result.txt', 'greeting.txt']
    sandbox.read_file("result.txt")
```

Reach the same files from a shell with `bash()`:

```python
bash("echo 'hello from bash' > note.txt && cat note.txt")   # shell over the workspace
```

> **Runnable:** [filesystem.py](examples/parselbox-basics/filesystem.py) · [bash.py](examples/parselbox-basics/bash.py)

### 4\. Packages & Networking

#### Packages

Pyodide supports pure-Python packages and many C-extension packages, which must be [pre-built for Pyodide](https://pyodide.org/en/stable/usage/packages-in-pyodide.html) — numpy, pandas, and more ship included.

```python
from parselbox import Parselbox, Mount

# preload Python + npm packages on startup
Parselbox(packages=["numpy", "pandas", "npm:lodash"])

# local wheel — mount its dir so Deno can read the host path
Parselbox(packages=["file:///host/wheels/pkg.whl"],
          mounts=[Mount("./wheels", "wheels", "ro")])

# remote wheel — needs network access
Parselbox(packages=["https://example.com/pkg.whl"], network=True)

# autoload as imports appear (only official domains when network=False)
Parselbox(allow_runtime_packages=True)
```

> [!NOTE]
> Package installs write straight to disk — a temp dir by default (wiped on exit). Set `package_dir` to persist them across sessions, so the next boot is instant with no re-download.

#### Networking

After initial package loading, network is blocked by default. Access is configured with Deno's permission controls via `--allow-net` / `--deny-net`. All HTTP from sandboxed code (requests, httpx, fetch) routes through Deno's `fetch()`.

```python
# Block everything (default)
Parselbox(network=False)

# Allow specific domains (Python API only)
Parselbox(network=["api.github.com:443"])

# Allow everything
Parselbox(network=True)
```

> [!NOTE]
> The CLI `--network` flag is a boolean toggle only. Domain allowlists are available via the Python API.

#### Proxy & Credential Injection

Pyodide is **not** a security boundary — sandboxed code can read env vars via `js('Deno.env.get("KEY")')`, so never pass real credentials in `env`. Instead, run a credential-injecting proxy on the host and lock the sandbox to it:

```python
async with Parselbox(
    network=["127.0.0.1:8900"],                 # sandbox can ONLY reach the proxy
    env={
        "OPENAI_BASE_URL": "http://127.0.0.1:8900/v1",
        "OPENAI_API_KEY": "phantom-token",      # harmless; the real key lives on the proxy
    },
) as sbx:
    await sbx.execute_code("import openai; openai.OpenAI().chat.completions.create(...)")
```

Most SDKs take a `base_url` override. For SDK-agnostic interception, set `HTTP_PROXY`/`HTTPS_PROXY`/`DENO_CERT` instead and route everything through a MITM proxy — Deno's `fetch()` honours them at the process level.

> **Runnable:** [basics.py](examples/parselbox-basics/basics.py)

### 5\. JavaScript Interop

Parselbox runs Python inside Deno's V8 engine via Pyodide, so Python and JavaScript share the same process memory — interop is seamless.

#### `js()` — Execute JavaScript from Python

```python
# Basic — auto converts args and results
js("return data.map(x => x * 2)", data=[1, 2, 3])  # [2, 4, 6]

# Callbacks — Python functions auto-proxied, no create_proxy needed
js("return items.filter(fn)", items=[1,2,3,4,5], fn=lambda x, *_: x > 3)  # [4, 5]

# Async + Web APIs (Intl, Crypto, URL, TextEncoder)
js("return crypto.randomUUID()")
```

Each `js()` call runs in a fresh, stateless scope. Python callables are auto-proxied and cleaned up after the call. Binary converts too — `Uint8Array`/`ArrayBuffer` results become Python `bytes`, and `bytes` arguments become `Uint8Array`s.

#### `require()` — Import npm Packages, Local Modules and WASM

```python
# npm packages — returns proxy + auto-injects in js() scope (alias= to rename)
lodash = require("lodash")
lodash.chunk([1, 2, 3, 4], 2)  # [[1, 2], [3, 4]]

# Callbacks work with require'd packages
lodash.sortBy(data, lambda x, *_: x["age"])

# Also available in js()
js("return lodash.invert({a: 1, b: 2})")

# Local TypeScript — compiled by Deno, hot-reloads; can import npm internally
require("./math_utils.ts").fibonacci(10)

# .wasm modules & WASI binaries load too — see WASM Tools

# Instances keep their methods — chain them
dayjs = require("dayjs")
dayjs("2026-06-15").add(30, "day").format("YYYY-MM-DD")   # "2026-07-15"

# Class constructors auto-detect `new`
color = require("color")
color("red").darken(0.5).hex()   # "#800000"

# Chains work with Python callbacks
lodash(data).filter(lambda x, *_: x["pay"] > 100).sortBy(lambda x, *_: -x["pay"]).value()
```

#### Deno Streaming (Large Files)

For files too large to fit in memory, use Deno streams via `resolvePath()`:

```python
js("""
    const path = resolvePath("sample.txt");
    const info = await Deno.stat(path);
    return { size: info.size, isFile: info.isFile };
""")
```

Python callbacks work inside streaming pipelines — Deno reads, JS parses, Python classifies each line.

#### Writing and Importing Modules

```python
# Python module — write it, import it
open("helpers.py", "w").write("def double(x): return x * 2")
from helpers import double
double(21)  # 42

# TypeScript module — compiled by Deno
open("transform.ts", "w").write("export function upper(s: string) { return s.toUpperCase(); }")
require("./transform.ts").upper("hello")  # "HELLO"
```

You can even compile a language to WebAssembly in-sandbox, then `require()` the output.

#### `bash()` — Shell Commands

A pure-JavaScript bash ([just-bash](https://github.com/vercel-labs/just-bash)) over the same workspace. Pipes and coreutils work, and `curl` is backed by `fetch`. Each call is isolated (`cd`/`export` don't persist); filesystem changes do.

```python
bash("echo hello > note.txt && cat note.txt | tr a-z A-Z")   # "HELLO"
bash("grep -rn hello . | wc -l")
bash("curl -s https://api.github.com/zen")                   # network rules still apply
```

> **Runnable:** [javascript.py](examples/parselbox-basics/javascript.py) · [bash.py](examples/parselbox-basics/bash.py)

### 6\. WASM Tools

Pyodide can only load packages built for it — so `pandoc`, `ruby` or `shellcheck` are out of reach, and there is no `apt-get` in a single-process sandbox. Parselbox closes that gap with **WASI**: any program compiled to WebAssembly becomes a tool, with no host install.

A missing capability is just a file.

#### Two kinds of `.wasm`

`require()` inspects the module and picks the right shape:

```python
# Library module (no imports) — its exports become methods
require("./fib.wasm").fib(20)                      # 6765

# Command module (a WASI program) — becomes a callable command
pandoc = require("./pandoc.wasm")
r = pandoc(["-f", "markdown", "-t", "html5"], stdin="# Report")
r["stdout"].decode()                               # '<h1 id="report">Report</h1>'
```

A command returns `{"exit": int, "stdout": bytes, "stderr": str, "missing": [...]}` — `missing` lists any syscalls the binary asked for that aren't implemented, so gaps surface as data rather than a crash.

> [!IMPORTANT]
> **Emscripten builds are not WASI builds.** Much of npm's "wasm" (`sql.js`, `ffmpeg.wasm`, `tesseract.js`) is compiled with Emscripten and needs its own JavaScript glue — import those as **npm packages** (`require("sql.js")`), not as bare `.wasm` files. Both routes work; `require()` tells you which one a binary needs.

```python
run(args=None, stdin="", env=None, preopens=None, argv0=None)
```

- **`stdin`** — `str` or `bytes`; **`stdout`** always comes back as `bytes`.
- **`preopens`** — grant extra guest directories, e.g. `preopens={"/usr": "vendor/usr"}` for a binary that expects its own tree.
- **`argv0`** — some binaries dispatch on their program name (lld becomes `wasm-ld` busybox-style).

#### Binaries as `bash()` commands

A WASI command binary (a `.wasm` exporting `_start`) in a mount's `bin/` directory becomes a shell command, usable alongside `bash()`'s JavaScript coreutils. Binaries are discovered per call, so a tool written mid-session works immediately.

```python
open("bin/pandoc.wasm", "wb").write(pandoc_bytes)

bash("pandoc -f markdown -t plain notes.md | head -3 | tr a-z A-Z")
#     ^^ compiled pandoc                      ^^ just-bash builtins
```

Mount a `bin/` folder read-only to ship a fixed toolset the agent can use but not modify — nothing installed on the host — or have it fetch a `.wasm` into `bin/` at runtime, which works even when the sandbox's network is restricted to a single allowlisted host.

You can even build one from source in-process — fetch a WASI clang + `wasm-ld` into `bin/`, compile C to `.wasm`, then `require()` the result. No host toolchain, nothing installed.

> [!NOTE]
> - Auto-detected as WASI `preview1` or `wasi_unstable` (preview0). Not supported: sockets, real sleeps, preview2 components.
> - Compiled modules are cached per path (invalidated on rebuild), so a 50MB binary compiles once per session.

> **Runnable:** [pandoc.py](examples/wasi/pandoc.py) — fetch a WASI binary · [compile_c.py](examples/wasi/compile_c.py) — compile C → wasm in-sandbox

### 7\. Progressive Disclosure

The `sbx` toolkit lets agents discover capabilities on demand instead of loading everything into context up front. Available as `sbx.*` inside the sandbox.

| Function | Description |
| :--- | :--- |
| `sbx.help()` | Returns a full guide to using the sandbox. |
| `sbx.info()` | Get sandbox environment info — context, packages, network, mounts, serve etc. |
| `sbx.search(pattern)` | Search tools across all namespaces by name, description, or parameter. |
| `sbx.inspect(tools)` | Get detailed schemas and documentation for tools. |
| `sbx.preview(data)` | Summarize large or nested data structures — preserves keys, truncates content. |

The sandbox also exposes a `help()` builtin for per-object introspection:

```python
# Sandbox guide
help()

# Namespace tree view — shows all methods with hierarchy
help(robot)
# Remote namespace 'robot' — methods execute on the host and return results.
# Methods:
# ├── sensors
# │   └── temperature()
# └── move()

# Tool details — description, parameters, output schema
help(robot.move)
# {"description": "Move robot to position.", "parameters": {...}, "output": {...}}

# Works on local objects too
help(len)
```

**Example:**

```python
# Discover what's available
sbx.info()

# Search for tools across all namespaces
sbx.search("repo|query")

# Get tool signatures before calling
sbx.inspect(["github.search_repositories", "db.query", "robot.move"])

# Parallel execution with .task
import asyncio
results = await asyncio.gather(*[api.fetch.task(id=i) for i in ids])

# Inspect unknown response structure
sbx.preview(results)
```

> **Runnable:** [toolkit.py](examples/parselbox-basics/toolkit.py)

### 8\. Generative UI

Agents can surface results two ways: **inline in the conversation** with `display()`, or as a **full web app** with `serve`.

#### Inline widgets — `display()`

Any HTML an agent passes to `display()` renders as a widget beneath its result, in hosts that support [MCP Apps](https://modelcontextprotocol.io).

```python
await sbx.execute_code("""
    display("<h1>Q3 Revenue</h1><p class='text-lg'>Up <b>12%</b> to $4.1M</p>")
""")
```

Tailwind and daisyUI are injected automatically, so plain markup is styled without a build step, and `pbx.call("/api/route", body)` inside the HTML reaches `@api` handlers when `serve` is on. `display()` also accepts a path to an HTML file in the workspace. One view per execution — the last call wins.

**On by default.** `run_mcp(ui=False)` turns it off, which stops advertising `display()` to the agent and drops the renderer from the tool. The rendered HTML is always on `result.view` regardless:

```python
result = await sbx.execute_code('display("<b>done</b>")')
result.view          # full HTML document, or None if display() wasn't called
```

#### Web apps — `serve`

The `serve` option starts a Deno HTTP server inside the sandbox — agents build full web apps on the fly.

```python
# SDK
sandbox = Parselbox(serve=3000)

# CLI
uvx parselbox --serve 3000
```

**Static Files:** Any files written to the Pyodide working directory are automatically served:

```python
open("index.html", "w").write("<h1>Hello World</h1>")
open("style.css", "w").write("h1 { color: blue; }")
```

Served at their own paths, with `/` resolving to `index.html`; uploaded and input files live under `/files/*`.

**API Handlers:** Define endpoints using FastAPI-style decorators:

```python
@api.get("/items")
def list_items(params):
    limit = int(params.get("limit", 10))
    return items[:limit]

@api.post("/items")
def create_item(body):
    return {"id": len(items) + 1, "name": body["name"]}
```

Routes are prefixed with `/api/` automatically. Verbs: `@api.get/post/put/patch/delete`.

Handlers can call MCP tools, context functions, and any sandbox code:

```python
@api.get("/dashboard")
async def dashboard(params):
    import asyncio
    sensors, orders = await asyncio.gather(
        robot.sensors.temperature.task(),
        store.get.task("/orders", params={"limit": 5}),
    )
    return {"temperature": sensors, "recent_orders": orders}
```

**Built-in Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/_upload` | POST | File upload (multipart form data) |
| `/_live` | GET | SSE stream — connected browsers refresh when static files change (on by default) |
| `/_routes` | GET | List registered API handlers |

```bash
curl -F "file=@photo.png" http://localhost:3000/_upload
# {"uploaded": [{"name": "photo.png", "path": "/files/photo.png", "size": 12345}]}
```

> **Runnable:** [display.py](examples/parselbox-basics/display.py) · [serve.py](examples/parselbox-basics/serve.py)

### 9\. Sandbox Hooks

Hooks intercept sandbox lifecycle events — log executions, approve tool calls, enforce policies. Pass them via the `hooks` parameter.

```python
from parselbox import Parselbox, Callback, ExecutionResult
from parselbox.hooks import Hook

class AuditHook(Hook):
    async def pre_execute(self, code: str):
        print(f"Executing: {code[:80]}...")

    async def post_execute(self, result: ExecutionResult):
        print(f"Result: {result.output}")

    async def pre_tool_call(self, callback: Callback):
        if "drop" in str(callback.kwargs).lower():
            raise PermissionError("DROP statements are blocked")

    async def post_tool_call(self, callback: Callback, result):
        print(f"Tool {callback.name} returned")

async with Parselbox(
    context={"db": db},
    hooks=[AuditHook()],
) as sbx:
    await sbx.execute_code("db.query(sql='SELECT 1')")
```

**`ElicitHook`** — a built-in hook that uses MCP elicitation for human-in-the-loop approval. Enable via `--elicit` (CLI) or `run_mcp(elicit=True)` (API). Only fires if the MCP client advertises elicitation capability — otherwise it's a no-op.

```bash
# CLI
uvx parselbox --mcp mcp.json --elicit

# API
await sandbox.run_mcp(elicit=True)
```

| Hook | Trigger | Use Cases |
|:---|:---|:---|
| `pre_execute` | Before code runs | Logging, policy checks, code sanitization |
| `post_execute` | After code completes | Audit trails, result validation |
| `pre_tool_call` | Before a context/MCP call | Approval gates, rate limiting, blocking |
| `post_tool_call` | After a context/MCP call returns | Logging, result transformation |

> **Runnable:** [hooks.py](examples/parselbox-basics/hooks.py)

---

## Configuration Reference

`Parselbox` has the following configuration options:

```python
from parselbox import Parselbox, Mount

sandbox = Parselbox(
    context=dict(db=db, notify=send_alert),   # Proxied functions and namespaces
    globals=dict(name="hi", threshold=0.5),   # Static values copied into sandbox
    files=["./input.txt"],                    # Read/write files at /files/
    mounts=[
        Mount("./datasets", "/data", "ro"),   # Read-only mount
        Mount("./workspace", "/work", "rw"),  # Read/write mount
    ],
    output_dir="./outputs",                   # Persist sandbox files
    packages=["numpy", "npm:lodash"],         # Install on startup (Python + npm)
    package_dir="./cache",                    # Persist package cache across sessions
    allow_runtime_packages=True,              # Auto-install from imports (default: False)
    network=True,                             # True, False, or ["domain:port", ...] (API only)
    mcp="./mcp.json",                         # Connect MCP servers (path or dict)
    serve=8080,                               # Enable web server on port
    memory=2048,                              # WASM memory limit in MB (default: 2048)
    timeout=60,                               # Execution timeout in seconds (default: 60, 0 disables)
    hooks=[AuditHook()],                      # Lifecycle hooks
    env={                                     # Custom env vars (available in Python os.environ)
        "OPENAI_BASE_URL": "http://proxy/v1", # SDK base_url overrides for reverse proxy
        "OPENAI_API_KEY": "phantom",          # Phantom tokens (real keys on proxy)
        "HTTP_PROXY": "http://proxy:8080",    # Deno-level proxy (filtered from os.environ)
        "DENO_CERT": "/path/to/ca.pem",       # Custom CA for MITM proxy
    },
)
```

---

## Architecture

Parselbox runs agent code in one Deno process with Pyodide (CPython in WebAssembly) — no containers, no VMs. The permission-jailed **sandbox** works in an isolated temp workspace, with no network and no host access beyond the mounts you grant; the **host** holds the credentials. Every tool call is a round-trip between them:

```
  1. exec       HOST ──▶ SANDBOX    your code runs, permission-jailed
  2. callback   HOST ◀── SANDBOX    code calls a tool as native Python
  3. result     HOST ──▶ SANDBOX    host runs it with the real credentials
```

Tools *look* like native Python inside the sandbox, but they execute on the host — so **credentials never enter the sandbox**.

---

## Security

Parselbox's boundary is **Deno's permission system** — the sandbox starts with nothing and gets only what you configure.

- **Filesystem** — isolated temp workspace (wiped on exit); read/write only to paths you pass (`files`, `mounts` as `ro`/`rw`, `output_dir`). Package-cache writes lock after startup unless `allow_runtime_packages=True`.
- **Network** — off by default (revoked before your code runs). Opt in with `network=True`, an allowlist `network=["host:port", ...]`, or `allow_runtime_packages=True` (package domains only). For authenticated APIs, front it with a proxy — see [Proxy & Credential Injection](#proxy--credential-injection).
- **Compiled tools (WASI)** — no sockets, so a binary has no network of its own; it sees only the mounts you grant (`ro` enforced by Deno), and a runaway is killed by the execution timeout.
- **Resource limits** — WASM memory capped per instance at the V8 level (default 2048 MB), JS heap capped, per-execution timeout (default 60s → `KeyboardInterrupt`), auto-reconnect if the Deno process dies.
- **Context bridge** — only the objects you pass are reachable, and only their public methods; MCP servers expose their full tool set.

## Related Work

- [Code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp) (Anthropic)
- [Code Mode](https://blog.cloudflare.com/code-mode/) (Cloudflare)
- [smolagents](https://huggingface.co/docs/smolagents/en/tutorials/secure_code_execution) (Hugging Face)
- [Deno + Pyodide Sandbox](https://til.simonwillison.net/deno/pyodide-sandbox) (Simon Willison)

Built with [Deno](https://deno.com), [Pyodide](https://pyodide.org), and [just-bash](https://github.com/vercel-labs/just-bash) (Vercel).
