"""
Hooks Examples - Intercept sandbox lifecycle events.

Available hooks:
- pre_execute(code)           — Before code runs
- post_execute(result)        — After code completes
- pre_tool_call(callback)     — Before a context/MCP call
- post_tool_call(callback, result) — After a context/MCP call returns
"""

import asyncio

from rich.console import Console

from parselbox import Parselbox, Callback, ExecutionResult
from parselbox.hooks import Hook

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


class AuditHook(Hook):
    """Logs all sandbox activity."""

    def __init__(self):
        self.log = []

    async def pre_execute(self, code: str):
        self.log.append(f"[EXEC] {code.strip()[:60]}")

    async def post_execute(self, result: ExecutionResult):
        status = "OK" if result.is_success else "FAIL"
        self.log.append(f"[RESULT] {status}: {result.output}")

    async def pre_tool_call(self, callback: Callback):
        self.log.append(f"[CALL] {callback.name}.{'.'.join(callback.path)}")

    async def post_tool_call(self, callback: Callback, result):
        self.log.append(f"[RETURN] {callback.name} -> {str(result)[:50]}")


class PolicyHook(Hook):
    """Block dangerous operations."""

    async def pre_tool_call(self, callback: Callback):
        args_str = str(callback.kwargs).lower()
        if "drop" in args_str or "delete" in args_str:
            raise PermissionError(f"Blocked: destructive operation in {callback.name}")

    async def pre_execute(self, code: str):
        if "os.system" in code or "subprocess" in code:
            raise PermissionError("Blocked: direct shell access not allowed")


class DB:
    def query(self, sql: str) -> list:
        return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    def execute(self, sql: str) -> str:
        return f"executed: {sql}"


async def demo_audit():
    section("Audit Hook")

    hook = AuditHook()
    async with Parselbox(context={"db": DB()}, hooks=[hook]) as sbx:
        await sbx.execute_code("x = 42")
        await sbx.execute_code("db.query(sql='SELECT * FROM users')")

    _console.print("\n[dim]Audit log:[/dim]")
    for entry in hook.log:
        _console.print(f"  {entry}")


async def demo_policy():
    section("Policy Hook")

    async with Parselbox(context={"db": DB()}, hooks=[PolicyHook()]) as sbx:
        await sbx.execute_code("db.execute(sql='SELECT 1')")
        await sbx.execute_code("db.execute(sql='DROP TABLE users')")
        await sbx.execute_code("import subprocess")


async def main():
    await demo_audit()
    await demo_policy()


if __name__ == "__main__":
    asyncio.run(main())
