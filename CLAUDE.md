# CLAUDE.md

Parselbox: a secure Python sandbox for AI agents — one `execute_code` MCP tool; external systems (MCP servers, REST/GraphQL, shells, host functions) appear as native Python objects inside it. Single process: Deno + Pyodide, no containers. It is an **execution layer** that plugs into any harness — harness-scope features (planning, memory, orchestration) are out of scope.

## Architecture (host ↔ sandbox)

```
Host (Python, holds credentials)                Deno subprocess (permission-jailed)
  main.py      Parselbox orchestrator             sandbox/main.ts     entry, registers "exec"
  rpc.py       JSON-lines RPC over stdio    <-->  sandbox/rpc.ts      counterpart
  bridge/      tools: http, graphql, shell,       sandbox/sandbox.ts  PyodideManager (mounts, timeout,
               mcp (OAuth), toolkit (sbx.*)                           packages, watcher, restart)
  mcp.py       exposes execute_code (FastMCP)     sandbox/serve.ts    @api routes, uploads, live reload
  view.py      display() → MCP Apps renderer      sandbox/wasi.ts     WASI preview1 shim — .wasm commands
  context.py   dispatches callbacks to bridges                        via require() and as bash() commands
  models.py    Callback / ExecutionResult / Mount sandbox/filesystem.ts  just-bash (pure-JS bash) + watcher
  prompt.py    agent guide (served by sbx.help()) sandbox/setup.ts    Pyodide boot + snapshot cache
  hooks.py     lifecycle + MCP elicitation        sandbox/bootstrap.py  runs INSIDE Pyodide: sbx toolkit,
                                                                      namespace proxies, tasks, js/require/bash
```

Two RPC flows: host→sandbox `exec` (run code), sandbox→host `callback` (a namespace proxy call → bridge executes with real creds → result returns). Tool calls made in sandbox code therefore run on the host; secrets never enter the sandbox.

## Commands

- `uv sync` — setup (Deno ≥2.x must be installed separately)
- `make test` — coverage + pytest over `tests/` (excludes bench); boots real sandboxes, ~7 min
- `uv run pytest tests/test_foo.py -q` — run one file; `make bench` — benchmarks
- `make check` — the CI gate: `deno lint` + `ruff` + `ruff format --check`, then `make test`. CI runs the same lint/format hooks via pre-commit.

## Gotchas (learned the hard way)

- **`bootstrap.py` runs inside Pyodide**, not on the host — only stdlib + pyodide/js imports. It ships in the wheel as source and is re-read at boot; its hash keys the Pyodide snapshot cache, so any edit invalidates the cache.
- **`prompt.py` strings are NOT `.format()`ed** — write literal single braces. Doubled `{{ }}` reach agents verbatim (this was a real bug).
- **Bytes over the boundary**: across the host RPC, `bytes` are `{"__b64__": ...}` (encode: `bridge._encode_bytes`; decode: `models._deserialize` and `ParselboxRpc._b64_hook`). In-process JS↔Python, typed arrays convert to `bytes` via a stringify replacer (`__pbx_bytes__`). Keep both directions symmetric when touching serialization.
- **`sandbox/*.ts` is Deno, not Node** — `deno.jsonc` sets `singleQuote` and lints on `recommended`. Run `deno fmt` / `deno lint` (or `make check`) before committing or the pre-commit gate fails in CI.
- Bridges connect lazily in `Parselbox.connect()`; a bridge that fails to connect logs an error but doesn't crash the sandbox.

## Conventions

- One logical change per commit. README examples must be **runnable as printed** — verify end-to-end before changing them (both Quick Start examples run live with `GITHUB_TOKEN` / `OPENAI_API_KEY`).
- Every user-facing feature stays documented in README.md; each User Guide chapter links its runnable twin in `examples/parselbox-basics/`.
- `.claude/skills/parselbox/` — skill for *using* Parselbox; keep it lean. `sbx.help()` (prompt.py) is the canonical agent guide.
- Release: bump `version` in `pyproject.toml`, then `git tag vX.Y.Z && git push --tags` — CI publishes to PyPI on `v*` tags.
