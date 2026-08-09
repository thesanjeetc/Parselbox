"""
Inline UI - display() renders HTML as a widget in the conversation.

In MCP Apps hosts (Claude Desktop, VS Code) the HTML appears beneath the tool
result. Other clients ignore it. SDK callers get the rendered document on
`result.view`.

- display(html)          — an HTML string
- display("page.html")   — a file in the working directory
- Tailwind + daisyUI are injected, so plain markup is styled with no build step
- pbx.call("/api/route", body) inside the HTML reaches @api handlers (needs serve=)

On by default; run_mcp(ui=False) turns it off. Over MCP it is only advertised to the
agent when the connected client can actually render widgets — result.view is
populated either way.
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_html():
    section("display() an HTML string")

    async with Parselbox() as sbx:
        result = await sbx.execute_code(
            dedent("""
            display('''
                <div class="card bg-base-200 shadow p-6">
                  <h1 class="text-2xl font-bold">Q3 Revenue</h1>
                  <p class="text-lg">Up <b>12%</b> to $4.1M</p>
                </div>
            ''')
            {"quarter": "Q3", "growth": 0.12}   # the return value is still for the agent
        """)
        )
        _console.print(f"output : {result.output}")
        _console.print(f"view   : {len(result.view)} bytes of HTML")
        _console.print(
            f"styled : tailwind={'tailwindcss' in result.view} "
            f"daisyui={'daisyui' in result.view}"
        )


async def demo_data_widget():
    section("A widget built from real data")

    async with Parselbox() as sbx:
        result = await sbx.execute_code(
            dedent("""
            rows = [
                {"region": "EMEA", "rev": 4.1},
                {"region": "AMER", "rev": 6.7},
                {"region": "APAC", "rev": 2.9},
            ]
            body = "".join(
                f"<tr><td>{r['region']}</td><td>${r['rev']}M</td></tr>" for r in rows
            )
            display(f'''
                <table class="table">
                  <thead><tr><th>Region</th><th>Revenue</th></tr></thead>
                  <tbody>{body}</tbody>
                </table>
            ''')
            sum(r["rev"] for r in rows)
        """)
        )
        _console.print(f"total  : {result.output}")
        _console.print(
            f"view   : contains all rows = "
            f"{all(x in result.view for x in ('EMEA', 'AMER', 'APAC'))}"
        )


async def demo_from_file():
    section("display() a file the agent wrote")

    async with Parselbox() as sbx:
        result = await sbx.execute_code(
            dedent("""
            open("report.html", "w").write(
                "<article class='prose'><h2>Weekly report</h2><p>All systems nominal.</p></article>"
            )
            display("report.html")
            "written"
        """)
        )
        _console.print(f"files  : {result.files}")
        _console.print(f"view   : {'Weekly report' in result.view}")

        # No display() -> no view.
        plain = await sbx.execute_code("2 + 2")
        _console.print(f"no display() -> view is {plain.view}")


async def main():
    await demo_html()
    await demo_data_widget()
    await demo_from_file()


if __name__ == "__main__":
    asyncio.run(main())
