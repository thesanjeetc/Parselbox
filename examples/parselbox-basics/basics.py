"""
Parselbox Basics - Core execution features.
"""

import asyncio
import tempfile
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_execution():
    section("Basic Execution")

    async with Parselbox() as sbx:
        await sbx.execute_code("2 + 2")

        await sbx.execute_code(
            dedent("""
            total = 100 * 5
            total
        """)
        )

        await sbx.execute_code(
            dedent("""
            import math
            import json
            json.dumps({"pi": round(math.pi, 4)})
        """)
        )


async def demo_state():
    section("State Persistence")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            import json
            data = []
        """)
        )

        await sbx.execute_code(
            dedent("""
            data.append({"name": "Alice", "score": 92})
            data.append({"name": "Bob", "score": 87})
            data
        """)
        )

        await sbx.execute_code(
            dedent("""
            avg = sum(d["score"] for d in data) / len(data)
            top = max(data, key=lambda d: d["score"])
            {"average": avg, "top_scorer": top["name"], "count": len(data)}
        """)
        )


async def demo_globals():
    section("Globals Injection")

    async with Parselbox(
        globals={
            "config": {"name": "MyApp", "version": "1.0"},
            "threshold": 0.75,
            "items": [1, 2, 3],
        }
    ) as sbx:
        await sbx.execute_code("config['name']")
        await sbx.execute_code("threshold")
        await sbx.execute_code("sum(items)")


async def demo_errors():
    section("Error Handling")

    async with Parselbox() as sbx:
        await sbx.execute_code("def broken(")
        await sbx.execute_code("1 / 0")
        await sbx.execute_code("ok = True")
        await sbx.execute_code("ok")


async def demo_packages():
    section("Packages")

    async with Parselbox(packages=["pytz"]) as sbx:
        await sbx.execute_code(
            dedent("""
            import pytz
            str(pytz.timezone('US/Eastern'))
        """)
        )

    async with Parselbox(allow_runtime_packages=True) as sbx:
        await sbx.execute_code(
            dedent("""
            import humanize
            humanize.naturalsize(1024**2 * 50)
        """)
        )

    async with Parselbox(packages=["numpy"]) as sbx:
        await sbx.execute_code(
            dedent("""
            import numpy as np
            int(np.array([1, 2, 3]).sum())
        """)
        )


async def demo_network():
    section("Network")

    async with Parselbox(packages=["requests"], network=True) as sbx:
        await sbx.execute_code(
            dedent("""
            import requests
            r = requests.get('https://httpbin.org/ip', timeout=5)
            r.json()
        """)
        )

    async with Parselbox(packages=["requests"], network=False) as sbx:
        await sbx.execute_code(
            dedent("""
            import requests
            try:
                requests.get('https://httpbin.org/ip', timeout=2)
                'connected'
            except Exception:
                'blocked'
        """)
        )


async def demo_env():
    section("Environment Variables")

    async with Parselbox(env={"APP_NAME": "demo", "APP_MODE": "testing"}) as sbx:
        await sbx.execute_code(
            dedent("""
            import os
            {"APP_NAME": os.environ["APP_NAME"], "APP_MODE": os.environ["APP_MODE"]}
        """)
        )


async def demo_timeout():
    section("Timeout")

    async with Parselbox(timeout=2) as sbx:
        await sbx.execute_code("while True: pass")


async def demo_files():
    section("write_file / read_file")

    async with Parselbox() as sbx:
        sbx.write_file("greeting.txt", "Hello from host!")
        await sbx.execute_code("open('greeting.txt').read()")

        await sbx.execute_code(
            dedent("""
            with open('response.txt', 'w') as f:
                f.write('Hello from sandbox!')
        """)
        )
        await sbx.execute_code("open('response.txt').read()")


async def demo_package_cache():
    section("Package Caching")

    with tempfile.TemporaryDirectory() as pkg_dir:
        async with Parselbox(packages=["pytz"], package_dir=pkg_dir) as sbx:
            await sbx.execute_code(
                dedent("""
                import pytz
                len(pytz.all_timezones)
            """)
            )

        async with Parselbox(packages=["pytz"], package_dir=pkg_dir) as sbx:
            await sbx.execute_code(
                dedent("""
                import pytz
                len(pytz.all_timezones)
            """)
            )


async def main():
    await demo_execution()
    await demo_state()
    await demo_globals()
    await demo_errors()
    await demo_packages()
    await demo_network()
    await demo_env()
    await demo_timeout()
    await demo_files()
    await demo_package_cache()


if __name__ == "__main__":
    asyncio.run(main())
