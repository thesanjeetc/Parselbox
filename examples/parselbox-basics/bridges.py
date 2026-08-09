"""
Bridge Examples - All bridge types in Parselbox.

Bridge types:
- Plain class       → auto-wrapped, methods exposed as tools
- Bridge subclass   → nested namespaces (sub-objects crawled)
- Bridge + log/recv → task-level progress and messaging
- HTTPBridge        → REST API with OpenAPI spec discovery
- GraphQLBridge     → GraphQL API with introspection
- ShellBridge       → local/Docker/SSH shell access
- MCPBridge         → MCP server connection
"""

import asyncio
import time
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox
from parselbox.bridge import Bridge, GraphQLBridge, HTTPBridge, ShellBridge

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


# ─── Plain class (auto-wrapped) ───────────────────────────────────────────────


class Calculator:
    """Plain class — automatically wrapped by Parselbox."""

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        return a + b

    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        return a * b


# ─── Nested Bridge hierarchy ──────────────────────────────────────────────────


class Sensors(Bridge):
    def temperature(self) -> float:
        """Read temperature in celsius."""
        return 23.5

    def pressure(self) -> float:
        """Read hydraulic pressure in bar."""
        return 175.2


class Arm(Bridge):
    def __init__(self):
        self.sensors = Sensors()

    def move(self, x: float, y: float, z: float = 0.0) -> dict:
        """Move arm to position."""
        return {"position": [x, y, z], "status": "reached"}

    def grip(self, force: int = 50) -> bool:
        """Grip with specified force."""
        return True


class Robot(Bridge):
    def __init__(self):
        self.arm = Arm()

    def status(self) -> dict:
        """Get robot status."""
        return {"battery": 87, "mode": "idle"}


# ─── Bridge with log/recv (task system) ───────────────────────────────────────


class Pipeline(Bridge):
    """Bridge with progress logging and message passing."""

    def process(self, items: list) -> dict:
        """Process items with progress updates."""
        for i, item in enumerate(items):
            time.sleep(0.1)
            self.log(f"Processing {i + 1}/{len(items)}: {item}")

            for msg in self.recv():
                self.log(f"Got message: {msg}")

        return {"processed": len(items)}


# ─── Pydantic models (auto-converted from dicts) ─────────────────────────────

from pydantic import BaseModel


class Coordinate(BaseModel):
    x: float
    y: float
    z: float = 0.0


class Navigator:
    def move_to(self, target: Coordinate) -> dict:
        """Move to target — dicts auto-convert to Pydantic models."""
        return {"position": [target.x, target.y, target.z], "status": "reached"}

    def distance(self, start: Coordinate, end: Coordinate) -> float:
        """Calculate distance between two points."""
        return (
            (end.x - start.x) ** 2 + (end.y - start.y) ** 2 + (end.z - start.z) ** 2
        ) ** 0.5


# ─── Demos ────────────────────────────────────────────────────────────────────


async def demo_plain_class():
    section("Plain Class (auto-wrapped)")

    async with Parselbox(context={"calc": Calculator()}) as sbx:
        await sbx.execute_code("calc.add(a=10, b=20)")
        await sbx.execute_code("calc.multiply(a=3, b=7)")
        await sbx.execute_code("dir(calc)")


async def demo_nested_bridge():
    section("Nested Bridge Hierarchy")

    async with Parselbox(context={"robot": Robot()}) as sbx:
        await sbx.execute_code("robot.status()")
        await sbx.execute_code("robot.arm.move(x=1.0, y=2.0, z=0.5)")
        await sbx.execute_code("robot.arm.grip(force=80)")
        await sbx.execute_code("robot.arm.sensors.temperature()")
        await sbx.execute_code("robot.arm.sensors.pressure()")
        await sbx.execute_code("dir(robot)")
        await sbx.execute_code("sbx.search('sensor|grip|move')")


async def demo_bridge_tasks():
    section("Bridge with log/recv (.task())")

    async with Parselbox(context={"pipeline": Pipeline()}) as sbx:
        await sbx.execute_code(
            dedent("""
            task = pipeline.process.task(items=["alpha", "beta", "gamma"])

            await asyncio.sleep(0.15)
            task.send("speed up!")

            s = await task.wait()
            {"state": s.state, "result": s.result, "log": task.tail(5)}
        """)
        )


async def demo_pydantic():
    section("Pydantic Models (dicts auto-convert)")

    async with Parselbox(context={"nav": Navigator()}) as sbx:
        await sbx.execute_code("nav.move_to(target={'x': 3, 'y': 4, 'z': 1})")
        await sbx.execute_code(
            dedent("""
            nav.distance(
                start={"x": 0, "y": 0},
                end={"x": 3, "y": 4}
            )
        """)
        )


async def demo_http_bridge():
    section("HTTPBridge (REST API)")

    api = HTTPBridge(base_url="https://jsonplaceholder.typicode.com")

    async with Parselbox(context={"api": api}, network=True) as sbx:
        await sbx.execute_code('api.get("/posts/1")')
        await sbx.execute_code('api.get("/users", params={"_limit": "2"})')
        await sbx.execute_code(
            dedent("""
            api.post("/posts", json={"title": "Hello", "body": "World", "userId": 1})
        """)
        )


async def demo_graphql_bridge():
    section("GraphQLBridge (introspection + queries)")

    countries = GraphQLBridge("https://countries.trevorblades.com/graphql")

    async with Parselbox(context={"gql": countries}, network=True) as sbx:
        await sbx.execute_code("gql.search('continent|country')")
        await sbx.execute_code(
            dedent("""
            gql.graphql(query='''
                {
                    continents {
                        name
                        code
                    }
                }
            ''')
        """)
        )
        await sbx.execute_code(
            dedent("""
            gql.graphql(query='''
                {
                    country(code: "GB") {
                        name
                        capital
                        currency
                        languages { name }
                    }
                }
            ''')
        """)
        )


async def demo_shell_bridge():
    section("ShellBridge (local bash)")

    shell = ShellBridge("bash")

    async with Parselbox(context={"sh": shell}) as sbx:
        await sbx.execute_code("sh.exec(command='echo hello from shell')")
        await sbx.execute_code("sh.exec(command='ls / | head -5')")
        await sbx.execute_code(
            dedent("""
            task = sh.exec.task(command='for i in 1 2 3; do echo step $i; sleep 0.1; done')
            s = await task.wait()
            {"state": s.state, "result": s.result, "log": task.tail(5)}
        """)
        )


async def demo_mcp_bridge():
    section("MCPBridge (MCP server)")

    mcp_config = {
        "mcpServers": {
            "deepwiki": {
                "type": "http",
                "url": "https://mcp.deepwiki.com/mcp",
            }
        }
    }

    async with Parselbox(mcp=mcp_config) as sbx:
        await sbx.execute_code("sbx.search('ask|read')")
        await sbx.execute_code("sbx.inspect(['deepwiki.ask_question'])")
        await sbx.execute_code(
            dedent("""
            deepwiki.ask_question(
                question='What is Pyodide?',
                repoName='pyodide/pyodide'
            )
        """)
        )


async def main():
    await demo_plain_class()
    await demo_nested_bridge()
    await demo_pydantic()
    await demo_bridge_tasks()
    await demo_http_bridge()
    await demo_graphql_bridge()
    await demo_shell_bridge()
    await demo_mcp_bridge()


if __name__ == "__main__":
    asyncio.run(main())
