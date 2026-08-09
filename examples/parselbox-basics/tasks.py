"""
Task Examples - Background execution with .task()

Append .task() to any bridge method to run it in the background.
Returns a ParselboxTask immediately with:

- task.status()         — Instant snapshot (state, elapsed, message, logfile)
- await task.wait(N)    — Wait up to N seconds, return status
- task.tail(n)          — Last n lines from the log
- task.logfile          — Path to full log file
- task.send(msg)        — Send message to running task
- task.result()         — Return value (None if not done)
- task.cancel()         — Cancel the task
- await task            — Wait until done, return result

Plain classes work for .task() out of the box.
Extend Bridge only when you need self.log() / self.recv() for task-level messaging.
"""

import asyncio
import time
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox
from parselbox.bridge import Bridge

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


class SimpleService:
    """Plain class — automatically wrapped. .task() works out of the box."""

    def fetch(self, id: int) -> dict:
        """Fetch a record (simulates 0.2s latency)."""
        time.sleep(0.2)
        return {"id": id, "data": f"Record {id}", "size": id * 100}


class LoggingService(Bridge):
    """Extends Bridge for self.log() and self.recv() inside tasks."""

    def process(self, items: list) -> dict:
        """Process items with progress logging."""
        results = []
        for i, item in enumerate(items):
            time.sleep(0.1)
            self.log(f"Processing item {i + 1}/{len(items)}: {item}")
            results.append({"item": item, "processed": True})
        return {"count": len(results), "results": results}

    def export(self, format: str = "csv") -> str:
        """Long-running export with progress."""
        rows = 50
        for i in range(rows):
            time.sleep(0.05)
            if i % 10 == 0:
                self.log(f"Exporting row {i}/{rows} ({format})")
        return f"export.{format}"

    def interactive(self, prompt: str = "> ") -> dict:
        """Interactive session — reads messages via self.recv()."""
        self.log(f"Session started (prompt: {prompt})")
        time.sleep(0.3)

        msgs = self.recv()
        for msg in msgs:
            self.log(f"Received: {msg}")

        time.sleep(0.2)
        msgs2 = self.recv()
        for msg in msgs2:
            self.log(f"Received: {msg}")

        return {"messages_received": len(msgs) + len(msgs2)}


async def demo_basic_task():
    section("Basic .task() (plain class)")

    async with Parselbox(context={"svc": SimpleService()}) as sbx:
        await sbx.execute_code(
            dedent("""
            task = svc.fetch.task(id=42)
            task.status()
        """)
        )
        await sbx.execute_code("await task.wait()")
        await sbx.execute_code("task.result()")


async def demo_parallel():
    section("Parallel Tasks with asyncio.gather")

    async with Parselbox(context={"svc": SimpleService()}) as sbx:
        await sbx.execute_code(
            dedent("""
            import time
            import asyncio

            start = time.time()
            seq = [svc.fetch(id=i) for i in range(5)]
            seq_time = round(time.time() - start, 2)

            start = time.time()
            par = await asyncio.gather(*[svc.fetch.task(id=i) for i in range(5)])
            par_time = round(time.time() - start, 2)

            {
                "sequential": f"{seq_time}s",
                "parallel": f"{par_time}s",
                "speedup": f"{seq_time/par_time:.1f}x",
            }
        """)
        )


async def demo_progress():
    section("Progress Tracking & Logs (extends Bridge)")

    async with Parselbox(context={"svc": LoggingService()}) as sbx:
        await sbx.execute_code(
            'task = svc.process.task(items=["alpha", "beta", "gamma", "delta"])'
        )
        await sbx.execute_code(
            dedent("""
            await asyncio.sleep(0.3)
            task.status()
        """)
        )
        await sbx.execute_code("task.tail(10)")
        await sbx.execute_code("await task.wait()")
        await sbx.execute_code("bash(f'grep gamma {task.logfile}')")


async def demo_logfile_bash():
    section("Log Files + Bash Integration")

    async with Parselbox(
        context={"svc": LoggingService(), "api": SimpleService()}
    ) as sbx:
        await sbx.execute_code(
            dedent("""
            task = svc.export.task(format="csv")
            other = api.fetch(id=1)
            s = await task.wait(timeout=10)

            log_lines = bash(f"wc -l < {task.logfile}").strip()
            last_log = bash(f"tail -1 {task.logfile}").strip()

            {
                "task_state": s.state,
                "task_result": s.result,
                "other_result": other,
                "log_lines": int(log_lines),
                "last_log": last_log,
            }
        """)
        )


async def demo_send_recv():
    section("task.send() / self.recv()")

    async with Parselbox(context={"svc": LoggingService()}) as sbx:
        await sbx.execute_code(
            dedent("""
            task = svc.interactive.task(prompt="$ ")

            await asyncio.sleep(0.1)
            task.send("hello from main")
            task.send("another message")

            s = await task.wait()
            {"status": s.state, "result": s.result, "log": task.tail(5)}
        """)
        )


async def demo_cancel():
    section("Task Cancellation")

    async with Parselbox(context={"svc": LoggingService()}) as sbx:
        await sbx.execute_code(
            dedent("""
            task = svc.process.task(items=list(range(20)))
            await asyncio.sleep(0.3)
            mid = task.status()

            task.cancel()
            await asyncio.sleep(0.1)
            final = task.status()

            {"mid_state": mid.state, "final_state": final.state, "log": task.tail(3)}
        """)
        )


async def demo_cross_step():
    section("Tasks Persist Across Execution Steps")

    async with Parselbox(context={"svc": LoggingService()}) as sbx:
        await sbx.execute_code("task = svc.export.task(format='json')")
        await sbx.execute_code("task.status()")
        await sbx.execute_code(
            dedent("""
            s = await task.wait(timeout=10)
            {"state": s.state, "result": s.result, "elapsed": s.elapsed}
        """)
        )


async def main():
    await demo_basic_task()
    await demo_parallel()
    await demo_progress()
    await demo_logfile_bash()
    await demo_send_recv()
    await demo_cancel()
    await demo_cross_step()


if __name__ == "__main__":
    asyncio.run(main())
