PARSELBOX_PROMPT = """
# Parselbox Sandbox Guide

## Overview

You have access to a **Parselbox sandbox** — a stateful Python execution environment with remote tool access.

Use it as a **scratchpad** — discover tools, explore APIs, transform data, test ideas, chain operations, iterate on results. Don't plan everything upfront — execute, observe, adjust. The sandbox persists between calls so you can build up state incrementally.

**Execution Model:**
- **Async context**: Can use `await` at top level if needed
- **Stateful**: Variables and packages persist between calls — work iteratively
- **Prefer return values**: `print()` output is captured to `result.stdout` for SDK callers but not shown to MCP clients — return data instead
- **Returns**: Last expression value; must be JSON-serializable (falls back to `repr()` if not)
- **Files**: New/modified files are automatically detected and returned
- **Delays**: Use `await asyncio.sleep()` instead of `time.sleep()` to yield to the event loop
- **Single-threaded**: WASM has no threads — `threading` and `ThreadPoolExecutor` raise `RuntimeError: can't start new thread`. Parallelise with `asyncio.gather` over `.task()` calls instead

**All remote namespace/MCP calls are synchronous by default** — append `.task()` for background execution with progress tracking.

---

## Sandbox Toolkit — Progressive Discovery

The `sbx` toolkit provides **progressive disclosure** — discover only what you need, when you need it, without filling up context.

### `sbx.info() -> dict`

**Start here.** Understand what's available — namespaces, capabilities, files, network access. Tells you what you're working with before you explore further.

**Returns:** `{context, environment, mounts, files, skills, serve}`
```python
sbx.info()
# -> {
#      context: {namespaces: [...], globals: [...]},
#      environment: {network: true, allow_runtime_packages: true, packages: [...]},
#      mounts: [...],
#      files: {path: "/files", items: [...]},
#      skills: "/mnt/skills",
#      serve: "http://localhost:8080"  # or None
#    }
```

---

### `sbx.search(pattern: str) -> dict`

**Find the right tool without browsing every namespace.** Searches tool names, descriptions, and parameter names across all namespaces simultaneously — MCP tools, nested namespaces, and HTTP endpoints in one call. Avoids filling up context by returning only what matches.

**Args:**
- `pattern`: Regex pattern to match against tool names, descriptions, and parameters.

**Returns:** `{"namespace (type)": [matches]}` — searches across MCP tools, nested namespaces, and HTTP endpoints simultaneously. Results grouped by namespace with type labels.
```python
sbx.search("invoice")
# -> {
#      "tracker (mcp)":       [{"path": "search_issues", "description": "Search issues by query..."}],
#      "store (http)":        [{"method": "GET", "path": "/invoices", "summary": "List invoices"}],
#      "billing (namespace)": [{"path": "payments.create_invoice", "description": "Create an invoice..."}],
#    }

sbx.search("create|delete")                          # regex OR
sbx.search("^get_")                                  # starts with
sbx.search("user.*profile")                          # regex pattern
sbx.search("temperature")                            # matches param names too
sbx.search("robot\\.arm")                            # full dotted path — all tools under robot.arm
```

---

### `sbx.inspect(tools: list) -> dict`

**Get schemas only when you're ready to call.** Returns parameter types, descriptions, and output schemas so you know exactly how to call a tool. Only fetch schemas for tools you plan to use — keeps context lean.

**Args:**
- `tools`: List of tool proxy objects

**Returns:** `{tool_name: {description, parameters, output}`
```python
sbx.inspect([tracker.search_issues])
# -> {"tracker.search_issues": {description: "...", parameters: {...}, output: {...}}

sbx.inspect([store.get_order, chat.send_message])
# -> {"store.get_order": {...}, "chat.send_message": {...}
```

---

### `sbx.preview(data: any) -> any`

**Explore unknown responses without blowing up context.** Truncates large lists, long strings, and deep nesting while preserving structure and keys. Use this before working with unfamiliar API responses — see the shape first, then access specific fields.

**Args:**
- `data`: Any data structure to summarize

**Returns:** Truncated version with same structure.
```python
sbx.preview(issues)
# -> [{id: 123, title: "Login fails...", assignee: {name: "alice", ...}, "...(23 more items)"]
```

---

## Execution Modes

All remote calls are **synchronous by default**. Append `.task()` to any bridge method to run it in the background. Tasks are awaitables with progress tracking, messaging, and non-blocking wait.

### 1. Synchronous (default)
Use when you need the result immediately.
```python
issues = tracker.search_issues(project="acme", status="open")
user = tracker.get_user(username="alice")
```

### 2. Background Tasks via `.task()`
Append `.task` to any bridge method to run it in the background. Returns a `Task` immediately.

```python
task = tracker.export_report.task(project="acme", format="csv")
```

**Check progress** — `status()` and `wait()` return `TaskStatus` with state, elapsed time, last log message, result (if done), error (if failed):
```python
task.status()              # instant check
await task.wait(timeout=5) # wait up to 5s, then return status
```
```
TaskStatus when running:  {"state": "running", "elapsed": 3.2, "message": "Row 1500/10000", "logfile": "..."}
TaskStatus when done:     {"state": "done",    "elapsed": 8.0, "message": "Complete", "result": {...}
TaskStatus when failed:   {"state": "failed",  "elapsed": 2.1, "message": "failed",  "error": "..."}
```

**Read logs** — every task streams output to `task.logfile`, a real file:
```python
task.tail(10)                          # last 10 lines
bash(f"grep ERROR {task.logfile}")     # search for issues
open(task.logfile).readlines()         # full log
```

**Parallel** — tasks are awaitables, use with `asyncio.gather`:
```python
import asyncio
data, orders, wiki = await asyncio.gather(
    robot.sensors.temperature.task(),
    store.get.task("/orders"),
    docs.read_wiki_contents.task(repoName="org/repo"),
)
```

**Interactive** — `send()` messages to running tasks, `tail()` to read output:
```python
session = vm.shell.task()
session.send("grep ERROR /var/log/app.log")
await asyncio.sleep(2)
session.tail(20)
session.send("exit")
await session.wait()
```

**Across execution steps** — tasks persist, check later:
```python
# Step 1: Start
task = pipeline.run.task(input="data.csv")
```
```python
# Step 2: Check, do other work
await asyncio.sleep(5)
task.status()
other = api.get("/stats")
```
```python
# Step 3: Collect or cancel
s = await task.wait(timeout=10)
# s.ok → done, s.result has the value
# s.state == "running" → still going, check s.logfile
```

**Task API:**
| | |
|---|---|
| `task.status()` | `TaskStatus` — instant snapshot |
| `await task.wait(timeout=N)` | `TaskStatus` — waits up to N seconds |
| `task.tail(n=5)` | Last n lines from the log |
| `task.logfile` | Path to full log file |
| `task.send(msg)` | Send message to running task |
| `task.result()` | Return value directly (`None` if not done) |
| `task.cancel()` | Cancel the task |
| `await task` | Wait until done, return raw result (raises on failure) |

- `.task` must be the **last** accessor before `()` — `tracker.export.task(...)` not `tracker.task.export(...)`
- Default (no `.task`) is synchronous — no `await` needed
- Tasks persist across execution steps — start in one call, check in the next

---

## Skills

Skills are reusable folders at `/mnt/skills/<name>/`, each with a `SKILL.md` describing purpose and instructions. They are **reference material, not callable tools** — discover and read them via `bash`, then import any Python modules they ship (the mount is on `sys.path`). `sbx.info()` reports whether `/mnt/skills` is mounted.

```python
# List available skills
bash("ls /mnt/skills/")

# Find skills by keyword (searches full SKILL.md content, not just metadata)
bash("grep -l chart /mnt/skills/*/SKILL.md")

# Read a skill's instructions
bash("cat /mnt/skills/data-viz/SKILL.md")

# Import Python modules shipped with the skill
from data_viz import charts

# Load JS/TS/WASM assets
utils = require("/mnt/skills/data-viz/transform.ts")
```

---

## Namespaces & Globals

**Namespaces** are remote tool collections listed in `sbx.info()` → `context.namespaces`:
- `name.* (mcp)` — MCP server connections
- `name.* (http)` — REST API bridges with their own `name.search()` for endpoint discovery (also included in `sbx.search()` results)
- `name.* (namespace)` — Python namespaces with nested sub-objects (e.g., `db.users.find()`, `cloud.storage.buckets.list()`)
- Use `sbx.search("regex")` to find tools — searches names, descriptions, and parameters
- Use `sbx.inspect([tool])` to get schemas before calling
- **MCP namespaces require keyword arguments; regular namespaces are flexible**


---

## Bash

`bash(command)` — execute shell commands. Returns stdout string. Raises on non-zero exit with stderr.

```python
# Search & explore
bash("grep -rn 'TODO' /mnt/project/src/ --include='*.py'")
bash("find /mnt/project -name '*.py' | wc -l")
bash("grep -rn 'def ' /mnt/project/ | sort -t: -k2 -n | head -10")

# Read files
bash("head -20 /mnt/project/src/auth.py")
bash("sed -n '40,50p' /mnt/project/src/auth.py")

# Write & edit
bash("echo 'content' > output.py")
bash("sed -i 's/old/new/g' /workspace/file.py")

# Data processing
bash("cat data.csv | awk -F, '{sum+=$3} END{print sum}'")
bash("curl -s https://api.example.com/data | jq '.results[]'")
```

**Commands:** `grep`, `find`, `sed`, `awk`, `sort`, `head`, `tail`, `cat`, `wc`, `cut`, `tr`, `uniq`, `xargs`, `jq`, `curl`, `diff`, `tee`, `ls`, `cp`, `mv`, `rm`, `mkdir`, `touch`, `echo`, `printf`, and more. Pipes, redirections, variables, loops all work. Run `bash("command --help")` for usage. Note: bash-specific syntax like process substitution (`<(...)`) is not supported.

**Behavior:**
- Working directory is `/workspace` — relative paths resolve there
- Each `bash()` call is isolated — `cd`, `export`, functions don't persist between calls. Filesystem changes persist.
- Python and bash share the same filesystem — writes in either are immediately visible to both
- `grep -r` auto-skips hidden directories (`.git`, `.env`, etc.)
- Use `--include='*.py'` or `--exclude-dir=node_modules` to filter
- Returns strings — parse in Python if you need structured data

---

## Filesystem

| Path | Access | Description |
|------|--------|-------------|
| Working directory | **Read/Write** | Use relative paths; files persist between calls |
| `/files/` | **Read/Write** | User-uploaded files  |
| `/mnt/{mount_name}/` | **Check `sbx.info()`** | Mounted host directories; access varies per mount |

**Rules:**
- ❌ **Never write to `/` or `/mnt/` paths marked read-only**
- ✅ **Always use relative paths** for output files
- Check `sbx.info()` → `mounts` to see access level (`read` or `read/write`) for each mount
- Use `bash("find ...")` or `bash("ls ...")` to explore files
- Files may be in `/files/`, mounted directories, or working directory

---

## Packages & Network

**Packages:**
- Check `sbx.info()` → `environment.packages` for packages installed at startup
- Python packages: auto-installed on import if `allow_runtime_packages: true`. If auto-install fails, try `import micropip; await micropip.install("package-name")` with the pip name (e.g. `python-pptx` for `pptx`)
- npm packages: use `require("package-name")` — cached after first import
- Other packages may already be installed (execution is stateful)

**Network:**
- Check `sbx.info()` → `environment.network`
- Values: `true` (full access), `false` (disabled), or `["host:port", ...]` (allowlist)
- Standard `requests` library works when enabled

---

## Workflow

**Adapt to complexity** — not every task requires full discovery.

**Skip exploration when:**
- You already have MCP server tools or function tools in your context
- Those tools match namespace names inside the sandbox (e.g., you have `tracker.search` tool and sandbox shows `tracker.* (mcp)`)
- They are the same — you already know the schemas, just call them directly
- Namespace is `(http)` — use `search()` to discover endpoints, then call them directly

**Use discovery when:**
- Namespaces/tools are unfamiliar
- You need to explore what's available
- Schema or parameters are unknown

**Discovery steps (when needed):**

1. **`sbx.info()`** — Understand available namespaces, capabilities, files, environment
2. **`sbx.search("regex")`** — Find tools across all namespaces by name, description, or parameter
3. **`sbx.inspect([...])`** — Get parameter schemas before calling unfamiliar tools
4. **Call tools** — Execute with correct parameters (keyword args for MCP)
5. **`sbx.preview(data)`** — Inspect unfamiliar or large responses

**Finding files:** Use `glob` or `os.listdir()` — `sbx.search()` searches tools, not files.

---

## Examples

### Namespace (nested)
```python
sbx.search("position|battery")
# -> {"robot (namespace)": [
#       {"path": "robot.arm.position", "description": "Get current arm XYZ position"},
#       {"path": "robot.battery", "description": "Get battery level and charging status"},
#       {"path": "robot.navigate", "description": "Move to position coordinates"},
#    ]}

sbx.inspect([robot.arm.grab])
robot.arm.grab(force=10.0)

# Background task
task = robot.arm.grab.task(force=10.0)
task.status()  # check progress
await task.wait()
```

### HTTP Bridge
```python
# Discover endpoints (if OpenAPI spec is loaded)
store.search("order")              # substring match in path + summary
store.search("GET /orders/*")      # method filter + glob on path
store.search("POST *")             # all POST endpoints
store.search("create|delete")      # regex
store.search("*")                  # list everything
# -> [{"method": "GET", "path": "/orders", "summary": "List orders"},
#     {"method": "POST", "path": "/orders", "summary": "Create order"},
#     {"method": "GET", "path": "/orders/{id}", "summary": "Get order by ID"}]

# Call endpoints — auth is handled on the host
store.get("/orders", params={"status": "pending"})
# -> {"status": 200, "ok": true, "data": [...]}

store.post("/orders", json={"item": "widget", "qty": 3})
store.put("/orders/123", json={"status": "shipped"})
store.delete("/orders/456")

# Parallel with asyncio.gather
import asyncio
orders, products = await asyncio.gather(
    store.get.task("/orders"),
    store.get.task("/products"),
)
```

### MCP
```python
sbx.search("issue|label")
# -> {"tracker (mcp)": [
#       {"path": "tracker.create_issue", "description": "Create a new issue"},
#       {"path": "tracker.search_issues", "description": "Search issues by query"},
#       {"path": "tracker.list_labels", "description": "List project labels"},
#    ]}

sbx.inspect([tracker.search_issues])
issues = tracker.search_issues(project="acme", status="open")  # keyword args only

# Background task
task = tracker.export_report.task(format="csv")
labels = tracker.list_labels()  # runs while export is in progress
report = await task
```

---

## JavaScript Interop

Python runs inside Deno via Pyodide (CPython compiled to WASM). Python and JavaScript share the same process memory — interop is handled by Pyodide's FFI, which is why JS, TS, npm, and WASM modules all work seamlessly from Python.

### `js(code, **kwargs) -> any`

Stateless — each call runs in a fresh scope, no shared state between calls. kwargs auto-converted, Python callables auto-proxied as callbacks.

```python
# Python data in → JS transforms → Python result out
js("return data.map(x => x * 2)", data=[1, 2, 3])  # [2, 4, 6]

# Python function called FROM JS — auto-proxied as callback
def score(text, *_):
    return len(text.split())

js(\"\"\"
    return texts.map(t => ({ text: t, score: scorer(t) }));
\"\"\", texts=["hello world", "hi"], scorer=score)
# -> [{"text": "hello world", "score": 2}, {"text": "hi", "score": 1}]

# Filter with Python logic, process in JS
js("return items.filter(fn)", items=[1,2,3,4,5], fn=lambda x, *_: x > 3)  # [4, 5]

# Web APIs — crypto, Intl, URL parsing, fetch
js("return crypto.randomUUID()")
js("return new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(n)", n=1234567.89)

# Streaming large files via Deno (constant memory) + Python callback per chunk
js(\"\"\"
    const file = await Deno.open(resolvePath(path), { read: true });
    const reader = file.readable.pipeThrough(new TextDecoderStream()).getReader();
    let lines = 0;
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        lines += value.split("\\n").length;
    }
    return lines;
\"\"\", path="/files/huge.csv")
```

### `require(name, alias=None)`

Import npm packages, local TypeScript/JavaScript, or WASM modules. Returns a wrapped module — method calls auto-convert between Python and JS types. Data returns become Python dicts/lists; class instances (dayjs, mathjs matrices, zod schemas) keep their methods. Class constructors (Fuse.js, Color) auto-detect `new`.

Alias is auto-generated from the package name (`"fuse.js"` → `fuseJs`, `"date-fns"` → `dateFns`) or filename (`"./data-utils.ts"` → `dataUtils`). Override with `alias=` if needed.

```python
# npm packages — alias auto-generated, cached after first import
lodash = require("lodash")
lodash.chunk([1, 2, 3, 4], 2)    # [[1, 2], [3, 4]]
lodash.groupBy(data, "type")      # {"a": [...], "b": [...]} — dict with attr access
lodash.invert({"a": "1"})         # {"1": "a"} — Python dict args work

# Packages with special names — no alias= needed
fuse = require("fuse.js")         # auto-alias: "fuseJs"
date_fns = require("date-fns")    # auto-alias: "dateFns"
yaml = require("js-yaml")         # auto-alias: "jsYaml"

# Chainable packages — instances keep their methods
dayjs = require("dayjs")
dayjs("2026-06-15").add(30, "day").format("YYYY-MM-DD")  # "2026-07-15"

mathjs = require("mathjs")
A = mathjs.matrix([[1, 2], [3, 4]])
mathjs.transpose(A).toArray()     # [[1, 3], [2, 4]] — proxy-to-proxy works

zod = require("zod")
schema = zod.object({"name": zod.string().min(1)})
schema.safeParse({"name": "ok"})  # {"success": true, "data": {...}

# Class constructors auto-detected (no js("new ...") needed)
fuse = require("fuse.js")
index = fuse(data, {"keys": ["title"]})  # auto Reflect.construct
index.search("python")                    # proxy methods work

color = require("color")
color("red").darken(0.5).mix(color("blue"), 0.5).hex()  # full chain

# Chains with Python callbacks
lodash(data).filter(lambda x, *_: x["salary"] > 100000).sortBy(lambda x, *_: -x["salary"]).value()

# All require()'d packages are also available inside js() by alias
js("return fuseJs.search('test')")
```

**`require()` vs `js()`**: Use `require()` for everything — it now handles chains, callbacks, constructors, and proxy-to-proxy passing. Fall back to `js()` only for complex JS syntax (destructuring, template literals, closures):
```python
# require() handles almost everything now
lodash.sortBy(data, lambda x, *_: x["salary"])   # callbacks with subscript access
lodash(data).filter(cb).sortBy(cb).value()         # chains with lambdas
fuse(data, opts)                                    # class constructors
color("red").darken(0.5).hex()                     # method chaining

# Use js() for JS-specific syntax
js("return items.map(({name, age}) => `${name} (${age})`)", items=data)
```

### Writing and importing code files

Write reusable functions and classes as files — import them across executions.

```python
# Write a Python module
with open("helpers.py", "w") as f:
    f.write("def process(x): return x * 2")
from helpers import process
process(21)  # 42

# Write TypeScript — compiled by Deno on the fly
with open("transform.ts", "w") as f:
    f.write("export function upper(s: string) { return s.toUpperCase(); }")
t = require("./transform.ts")  # auto-alias: "transform"
t.upper("hello")  # "HELLO"

# Write to mounted dir — persists between sessions
with open("/mnt/libs/utils.py", "w") as f:
    f.write("def calc(x): return x ** 2")
```

Mounted directories are on `sys.path` — Python imports work directly. JS/TS files use `require()` with the file path. All remote namespaces are available as builtins — accessible from any imported module or script without explicit imports.

### `resolvePath(path) -> str`

Auto-injected in `js()`. Resolves VFS paths to host filesystem paths for Deno file APIs.

### Direct JS globals

```python
from js import Math, Date, JSON, console, fetch, Deno
```

---

## Quick Reference

- Start with `sbx.info()` when exploring unfamiliar environments
- Use `sbx.search("regex")` to find tools across all namespaces
- Use `sbx.inspect()` before calling unfamiliar tools
- Use `glob` to find files — `sbx.search()` searches tools, not files
- Skip discovery if you already have matching MCP/function tools
- Use keyword arguments for MCP namespace calls
- Use relative paths for output files
- Use `.task()` for background/parallel execution — `await task` or `await task.wait()`
- Return JSON-serializable results
- Work iteratively (execution is stateful)
"""

PARSELBOX_SERVE_PROMPT = """
# Dynamic Web Apps

Build dynamic web apps on the fly with the serve feature inside the Parselbox sandbox.


### Static File Serving

Any files written to the working directory or at "/files/*" are automatically served:

```python
with open("index.html", "w") as f:
    f.write("<h1>Hello World</h1>")

with open("style.css", "w") as f:
    f.write("h1 { color: blue; }")
```

Accessible at:
- `/` → index.html
- `/style.css` → style.css
- `/files/*` → uploaded/input files

---

### API Handlers

Define HTTP endpoints using FastAPI-style decorators:

```python
@api.get("/users")
def list_users(params):
    # params = query string as dict
    # e.g., /api/users?limit=10 → {"limit": "10"}
    return [{"id": 1, "name": "Alice"}]

@api.post("/users")
def create_user(body):
    # body = parsed JSON from request body
    return {"created": body["name"]}

@api.put("/users")
def update_user(body):
    return {"updated": True}

@api.delete("/users")
def delete_user(body):
    return {"deleted": body["id"]}
```

Routes auto-prefix with `/api/`:
- `@api.get("/users")` → `GET /api/users`

**Decorators:** `@api.get`, `@api.post`, `@api.put`, `@api.patch`, `@api.delete`

**Response:** Return any JSON-serializable value (dict, list, string, number). Errors return `{"error": "...", "traceback": "..."}` with status 500.

Both sync and async handlers work. Handlers can call any namespace tool:

```python
# Sync handler calling namespace tools
@api.get("/report")
def report(params):
    return {"issues": tracker.search_issues(status="open"), "labels": tracker.list_labels()}

# Async handler with parallel tasks across different namespaces
@api.get("/dashboard")
async def dashboard(params):
    import asyncio
    sensors, orders, wiki = await asyncio.gather(
        robot.arm.sensors.temperature.task(),
        store.get.task("/orders", params={"limit": 5}),
        docs.read_wiki_structure.task(repoName="org/repo"),
    )
    return {"temperature": sensors, "recent_orders": orders, "docs": wiki}
```

---

### File Upload

`POST /_upload` accepts multipart form data:

```javascript
// Frontend
const formData = new FormData();
formData.append('file', fileInput.files[0]);

const res = await fetch('/_upload', { method: 'POST', body: formData });
const { uploaded } = await res.json();
// uploaded = [{ name: "photo.png", path: "/files/photo.png", size: 12345 }]
```

- Files written to `/files/`
- Accessible via HTTP: `GET /files/photo.png`
- Python can read: `open("/files/photo.png", "rb")`

---

### Live Reload

Automatically enabled. When Python writes static files (`.html`, `.css`, `.js`, `.png`, etc.), connected browsers refresh automatically.

---

### Built-in Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/_upload` | POST | File upload (multipart) |
| `/_live` | GET | SSE stream for live reload |
| `/_routes` | GET | List registered API handlers |

---

### Summary

| Feature | How |
|---------|-----|
| Serve HTML/CSS/JS | Write to `/workspace/` |
| API endpoints | `@api.get/post/put/delete` |
| File uploads | `POST /_upload` → `/files/` |
| Serve uploads | `GET /files/*` |
| Live reload | Automatic on static file changes |
| Context tools | Call MCP, namespaces, functions from handlers |

"""

PARSELBOX_UI_PROMPT = """

---

## Inline UI — `display(content)`

Render HTML inline in the conversation. Hosts that support MCP Apps (Claude Desktop,
VS Code) show it as a widget under your result; other clients ignore it.

```python
display("<h1>Q3 Revenue</h1><p class='text-lg'>Up <b>12%</b> to $4.1M</p>")
display("dashboard.html")     # or a file you wrote in the working directory
```

- **Tailwind and daisyUI are already loaded** — use their classes directly, no build step,
  no `<link>` tags. Plain semantic markup also looks fine.
- One view per execution — the last `display()` wins.
- Still return data as your final expression — `display()` is for the human, the return
  value is for you.
- Images from any URL work, as do inline SVG and base64 `data:` URIs.

**Making it interactive** — the view is a real web page, so it needs a backend. That's
`@api` handlers plus `pbx.call()` (needs `serve`):

```python
@api.get("/repos")
def repos(params):
    return github.search_repositories(query=params.get("q", "python"))

display('''
  <button class="btn btn-primary" onclick="load()">Search</button>
  <div id="out"></div>
  <script>
    async function load() {
      const data = await pbx.call("/api/repos?q=rust", undefined, { method: "GET" });
      document.getElementById("out").textContent = JSON.stringify(data);
    }
  </script>
''')
```

- Handlers are live Python in **this** sandbox — they keep your variables, imports,
  namespaces and files. State held in a global stays server-side; the browser never sees it.
- `pbx.call(path, body)` POSTs by default; pass `{ method: "GET" }` for a GET. A path
  without a leading `/` is prefixed with `/api/`.
- **Fetch the network from Python, not from the view.** A view may not call arbitrary
  origins directly — do the request in an `@api` handler (or before `display()`) and hand
  the result to the view.
"""
