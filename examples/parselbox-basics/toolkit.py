"""
Toolkit Examples - The sbx introspection and utility toolkit.

Available methods:
- sbx.info()      - Environment overview
- sbx.search()    - Search tools across all namespaces
- sbx.inspect()   - Get tool signatures and schemas
- sbx.preview()   - Truncate large data for inspection
- sbx.help()      - Full sandbox guide
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


class API:
    """Mock API for demonstrations."""

    def fetch(self, id: int) -> dict:
        """Fetch a resource by ID."""
        return {"id": id, "data": f"Resource {id}"}

    def search(self, query: str, limit: int = 10) -> list:
        """Search for resources."""
        return [{"id": i, "match": query} for i in range(limit)]

    def delete(self, id: int) -> bool:
        """Delete a resource."""
        return True


async def demo_info():
    section("sbx.info()")

    async with Parselbox(
        globals={"threshold": 0.5},
        context={"api": API()},
        packages=["numpy"],
        network=True,
    ) as sbx:
        await sbx.execute_code("sbx.info()")


async def demo_search():
    section("sbx.search()")

    async with Parselbox(context={"api": API()}) as sbx:
        await sbx.execute_code("sbx.search('fetch|search|delete')")


async def demo_inspect():
    section("sbx.inspect()")

    async with Parselbox(context={"api": API()}) as sbx:
        await sbx.execute_code("sbx.inspect(['api.fetch', 'api.search'])")


async def demo_preview():
    section("sbx.preview()")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            large = {
                'items': list(range(100)),
                'text': 'x' * 500,
                'nested': {'a': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
            }
            sbx.preview(large)
        """)
        )


async def main():
    await demo_info()
    await demo_search()
    await demo_inspect()
    await demo_preview()


if __name__ == "__main__":
    asyncio.run(main())
