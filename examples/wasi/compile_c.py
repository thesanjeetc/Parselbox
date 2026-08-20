"""
Compile C to WebAssembly inside the sandbox.

The sandbox can't run a native compiler — but it can run one that is itself
WebAssembly. This fetches clang + wasm-ld (binji's wasm-clang, clang 8.0.1)
into bin/ so they become bash() commands, unpacks the sysroot, then compiles a
C program to a .wasm and calls it — no host toolchain, nothing installed.

Fetches ~57MB of toolchain on first run, so it needs network access.
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)

TOOLCHAIN = "https://binji.github.io/wasm-clang"

PROGRAM = """
int fib(int n) {
    return n < 2 ? n : fib(n - 1) + fib(n - 2);
}
"""


async def main():
    _console.rule("Compile C -> WebAssembly, in-sandbox", style="dim")
    async with Parselbox(network=["binji.github.io:443"], timeout=300) as sbx:
        result = await sbx.execute_code(
            dedent(f"""
            import os, tarfile
            os.makedirs("bin", exist_ok=True)
            js('''
                async function grab(url, path) {{
                    const res = await fetch(url);
                    await Deno.writeFile(resolvePath(path), new Uint8Array(await res.arrayBuffer()));
                }}
                await grab("{TOOLCHAIN}/clang", "bin/clang.wasm");
                await grab("{TOOLCHAIN}/lld", "bin/wasm-ld.wasm");
                await grab("{TOOLCHAIN}/sysroot.tar", "sysroot.tar");
            ''')
            tarfile.open("sysroot.tar").extractall(".")
            os.remove("sysroot.tar")
            clang_mb = os.path.getsize("bin/clang.wasm") // 1_000_000
            ld_mb = os.path.getsize("bin/wasm-ld.wasm") // 1_000_000
            f"toolchain ready: clang {{clang_mb}}MB, wasm-ld {{ld_mb}}MB"
        """)
        )
        _console.print(result.output)

        result = await sbx.execute_code(
            dedent("""
            open("fib.c", "w").write(PROGRAM)
            flags = "-isysroot / -internal-isystem /include -internal-isystem /lib/clang/8.0.1/include"
            bash(f"clang -cc1 -emit-obj {flags} -O2 -o fib.o -x c fib.c")
            bash("wasm-ld --no-entry --export=fib fib.o -o fib.wasm")

            fib = require("./fib.wasm")
            {"fib(20)": fib.fib(20), "wasm_bytes": len(open("fib.wasm", "rb").read())}
        """).replace("PROGRAM", repr(PROGRAM))
        )
        _console.print(result.output)
        _console.print(f"detected files: {result.files}")


if __name__ == "__main__":
    asyncio.run(main())
