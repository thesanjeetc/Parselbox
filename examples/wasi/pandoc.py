"""
Compiled Tools (WASI) - run any compiled binary as a sandbox tool.

Pyodide can only load packages built for it, and there is no apt-get in a
single-process sandbox. WASI closes the gap: a .wasm is a tool, with no install.

- require("./x.wasm")   — library module -> exports; WASI command -> callable
- bin/*.wasm            — becomes a bash() command, alongside the JS coreutils
- Mount("./pack", ro)   — a capability pack: usable, not modifiable

This example fetches pandoc (~59MB) on first run, so it needs network access.
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)

PANDOC_URL = "https://unpkg.com/pandoc-wasm@1.1.0/src/pandoc.wasm"

REPORT = "# Q3 Report\n\n**Revenue:** up 12%\n\n| region | rev |\n|---|---|\n| EMEA | 4.1M |\n"


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_acquire_tool():
    section("Acquiring a tool the sandbox didn't have")

    async with Parselbox(network=["unpkg.com:443"], timeout=180) as sbx:
        result = await sbx.execute_code(
            dedent(f"""
            import os
            os.makedirs("bin", exist_ok=True)

            size = js('''
                const res = await fetch("{PANDOC_URL}");
                const bytes = new Uint8Array(await res.arrayBuffer());
                await Deno.writeFile(resolvePath("bin/pandoc.wasm"), bytes);
                return bytes.length;
            ''')
            f"installed pandoc: {{size / 1e6:.0f}} MB"
        """)
        )
        _console.print(result.output)

        result = await sbx.execute_code(
            dedent("""
            open("report.md", "w").write(REPORT)
            bash("pandoc -f markdown -t html5 report.md")
        """).replace("REPORT", repr(REPORT))
        )
        _console.print(result.output)

        result = await sbx.execute_code(
            dedent("""
            pandoc = require("./bin/pandoc.wasm")
            r = pandoc(["-f", "markdown", "-t", "docx", "-o", "report.docx"],
                       stdin=open("report.md").read())
            data = open("report.docx", "rb").read()
            {"exit": r["exit"], "bytes": len(data), "is_docx": data[:2] == b"PK"}
        """)
        )
        _console.print(result.output)
        _console.print(f"detected files: {result.files}")

        result = await sbx.execute_code(
            """bash("echo '<h1>Plans</h1><ul><li>Pro: $49</li></ul>' | pandoc -f html -t gfm")"""
        )
        _console.print(result.output)


async def demo_pipeline():
    section("In a bash pipeline, beside the JS coreutils")

    async with Parselbox(network=["unpkg.com:443"], timeout=180) as sbx:
        await sbx.execute_code(
            dedent(f"""
            import os
            os.makedirs("bin", exist_ok=True)
            js('''
                const res = await fetch("{PANDOC_URL}");
                await Deno.writeFile(resolvePath("bin/pandoc.wasm"),
                                     new Uint8Array(await res.arrayBuffer()));
            ''')
        """)
        )

        result = await sbx.execute_code(
            dedent("""
            open("notes.md", "w").write("# Alpha\\n\\n# Beta\\n\\n# Gamma\\n")
            bash("pandoc -f markdown -t plain notes.md | head -3 | tr a-z A-Z")
        """)
        )
        _console.print(result.output)


async def main():
    await demo_acquire_tool()
    await demo_pipeline()


if __name__ == "__main__":
    asyncio.run(main())
