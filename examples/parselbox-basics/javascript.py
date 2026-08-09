"""
JavaScript Interop Examples - js(), require(), resolvePath(), and cross-language workflows.

Python runs inside Deno via Pyodide. They share the same process memory:

- js(code, **kwargs)  — Run JS with Python vars injected. Stateless per call.
- require(name)       — Import npm packages or local TS/JS modules. Cached.
- resolvePath(path)   — Resolve VFS paths to real host paths (inside js() only).
- from js import X    — Direct access to JS globals (Math, Date, JSON, Deno, etc.)
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_js_basics():
    section("js() Basics")

    async with Parselbox() as sbx:
        await sbx.execute_code('js("return 2 + 2")')
        await sbx.execute_code('js("return data.map(x => x * 2)", data=[1, 2, 3])')
        await sbx.execute_code(
            'js("return items.filter(fn)", items=[1,2,3,4,5], fn=lambda x, *_: x > 3)'
        )
        await sbx.execute_code(
            dedent("""
            js(
                "return new Intl.NumberFormat('en-US', {style:'currency', currency:'USD'}).format(n)",
                n=1234.5
            )
        """)
        )
        await sbx.execute_code('js("return crypto.randomUUID()")')


async def demo_js_callbacks():
    section("js() with Python Callbacks")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            def score(text, *_):
                return len(text.split())

            js(\"\"\"
                return texts.map(t => ({ text: t, score: scorer(t) }));
            \"\"\", texts=["hello world", "hi"], scorer=score)
        """)
        )
        await sbx.execute_code(
            dedent("""
            js(\"\"\"
                return items.sort((a, b) => cmp(a, b));
            \"\"\", items=[3, 1, 4, 1, 5], cmp=lambda a, b, *_: a - b)
        """)
        )


async def demo_require_npm():
    section("require() — npm Packages")

    async with Parselbox(
        packages=["npm:lodash"],
        allow_runtime_packages=True,
    ) as sbx:
        await sbx.execute_code(
            dedent("""
            lodash = require("lodash")
            lodash.chunk([1, 2, 3, 4, 5, 6], 2)
        """)
        )
        await sbx.execute_code("lodash.uniq([1, 1, 2, 2, 3, 3])")
        await sbx.execute_code('js("return lodash.invert({a: 1, b: 2})")')
        await sbx.execute_code(
            dedent("""
            data = [
                {"name": "Alice", "age": 30},
                {"name": "Bob", "age": 25},
                {"name": "Charlie", "age": 35},
            ]
            lodash.sortBy(data, lambda x, *_: x["age"])
        """)
        )


async def demo_require_typescript():
    section("require() — Local TypeScript")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            with open("math_utils.ts", "w") as f:
                f.write(\"\"\"
            export function fibonacci(n: number): number {
                if (n <= 1) return n;
                let a = 0, b = 1;
                for (let i = 2; i <= n; i++) [a, b] = [b, a + b];
                return b;
            }
            export function isPrime(n: number): boolean {
                if (n < 2) return false;
                for (let i = 2; i <= Math.sqrt(n); i++) {
                    if (n % i === 0) return false;
                }
                return true;
            }
            export function range(start: number, end: number): number[] {
                return Array.from({length: end - start}, (_, i) => start + i);
            }
            \"\"\")
        """)
        )
        await sbx.execute_code(
            dedent("""
            math = require("./math_utils.ts")
            math.fibonacci(10)
        """)
        )
        await sbx.execute_code("math.isPrime(17)")
        await sbx.execute_code("[n for n in math.range(2, 30) if math.isPrime(n)]")


async def demo_require_hot_reload():
    section("require() — Hot Reload")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            with open("greeter.ts", "w") as f:
                f.write('export function greet(name: string) { return `Hello ${name}`; }')
        """)
        )
        await sbx.execute_code(
            dedent("""
            g = require("./greeter.ts")
            g.greet("World")
        """)
        )

        await sbx.execute_code(
            dedent("""
            with open("greeter.ts", "w") as f:
                f.write('export function greet(name: string) { return `Howdy ${name}!`; }')
        """)
        )
        await sbx.execute_code(
            dedent("""
            g = require("./greeter.ts")
            g.greet("World")
        """)
        )


async def demo_resolve_path():
    section("resolvePath() — Deno File Streaming")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            with open("sample.txt", "w") as f:
                for i in range(100):
                    f.write(f"Line {i}: {'x' * 50}\\n")
        """)
        )
        await sbx.execute_code(
            dedent("""
            js(\"\"\"
                const path = resolvePath("sample.txt");
                const info = await Deno.stat(path);
                return { size: info.size, isFile: info.isFile };
            \"\"\")
        """)
        )
        await sbx.execute_code(
            dedent("""
            count = js(\"\"\"
                const content = await Deno.readTextFile(resolvePath("sample.txt"));
                return { size: content.length };
            \"\"\")
            count
        """)
        )


async def demo_js_globals():
    section("Direct JS Globals")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            from js import Math
            {
                "pi": round(Math.PI, 5),
                "e": round(Math.E, 5),
                "sqrt2": round(Math.sqrt(2), 5),
            }
        """)
        )


async def demo_cross_language():
    section("Cross-Language Workflow (Python + JS + Bash)")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            import json

            data = [{"name": f"item_{i}", "value": i * 7} for i in range(5)]

            transformed = js(\"\"\"
                return data.map(item => ({
                    ...item,
                    label: item.name.toUpperCase(),
                    hex: '0x' + item.value.toString(16)
                }));
            \"\"\", data=data)

            with open("output.json", "w") as f:
                json.dump(transformed, f, indent=2)

            bash("head -8 output.json")
        """)
        )


async def demo_write_and_import():
    section("Write & Import Modules")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            with open("helpers.py", "w") as f:
                f.write(\"\"\"
            def double(x):
                return x * 2

            def greet(name):
                return f'Hello {name}!'
            \"\"\")

            from helpers import double, greet
            {"double": double(21), "greet": greet("World")}
        """)
        )


async def main():
    await demo_js_basics()
    await demo_js_callbacks()
    await demo_require_npm()
    await demo_require_typescript()
    await demo_require_hot_reload()
    await demo_resolve_path()
    await demo_js_globals()
    await demo_cross_language()
    await demo_write_and_import()


if __name__ == "__main__":
    asyncio.run(main())
