# WASI / Compiled Tools

Compiled binaries as sandbox tools. Any WASI (WebAssembly System Interface)
`.wasm` program becomes a tool: `require()` turns it into a Python callable, and
dropping it in a `bin/` directory makes it a `bash()` command — all in-process,
nothing installed on the host.

| File | Description |
| --- | --- |
| `pandoc.py`    | **Use** a prebuilt binary — fetch `pandoc.wasm` and run it via `require()` and on the `bash()` PATH. |
| `compile_c.py` | **Build your own** — fetch a WASI clang + `wasm-ld`, compile a C program to `.wasm`, then call it. |

Both fetch their binaries on first run, so they need network access
(`compile_c.py` pulls ~57MB of toolchain).

```bash
uv run python examples/wasi/pandoc.py
uv run python examples/wasi/compile_c.py
```
