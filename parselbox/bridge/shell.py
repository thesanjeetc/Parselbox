"""Shell bridge — run commands via local shell, Docker, SSH, kubectl."""

import asyncio
import contextlib
import os
import signal
import shlex
from collections import deque

from parselbox.bridge import Bridge, _current_task

_MAX_OUTPUT = 16 * 1024 * 1024
_KEEP = 4000

_STREAM_LIMIT = 64 * 1024 * 1024


class ShellBridge(Bridge):
    """Shell access with stdout -> self.log() and stdin <- self.recv().

    Commands are piped via stdin — works universally across all backends
    without needing to know how each one accepts command arguments.

    Examples:
        ShellBridge()                                           # local bash
        ShellBridge("dash")                                     # local dash
        ShellBridge("docker exec -i my-container sh")           # Docker
        ShellBridge("ssh -T user@host")                         # SSH
        ShellBridge("kubectl exec -i deploy/worker -- sh")      # Kubernetes
    """

    _pbx_type = "shell"

    def __init__(self, command: str | None = None, max_output: int = _MAX_OUTPUT):
        self._command = command or "bash"
        self._parts = shlex.split(self._command)
        self._max_output = max_output

    async def _pbx_connect(self):
        """Verify the shell command works before accepting calls."""
        proc = await self._spawn()
        try:
            proc.stdin.write(b"echo ok\n")
            proc.stdin.close()
            out = await asyncio.wait_for(proc.stdout.read(), timeout=10)
            await proc.wait()
        except (asyncio.TimeoutError, OSError):
            await self._kill(proc)
            raise ConnectionError(f"Shell not reachable: {self._command}")
        if proc.returncode != 0 or b"ok" not in out:
            raise ConnectionError(
                f"Shell not reachable: {self._command} (exit {proc.returncode})"
            )
        await super()._pbx_connect()

    @property
    def __doc__(self):
        return (
            "Shell access — local, Docker, SSH, kubectl, or any REPL. Same interface.\n"
            "exec(command) runs a command and returns {exit_code, output, lines} — the full\n"
            "  stdout. Past 16MB the middle is dropped and `truncated` says so.\n"
            "  To fetch a file, base64 it and decode here:\n"
            "      import base64\n"
            '      r = exec("base64 -w0 /tmp/out.mp4")   # or: base64 file | tr -d "\\n"\n'
            '      open("out.mp4", "wb").write(base64.b64decode(r["output"]))\n'
            "shell() opens an interactive session via .task() — use task.send() for commands, task.tail() for output."
        )

    async def _spawn(self):
        return await asyncio.create_subprocess_exec(
            *self._parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=_STREAM_LIMIT,
        )

    async def _kill(self, proc):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except OSError:
            with contextlib.suppress(OSError):
                proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
            return
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            with contextlib.suppress(OSError):
                proc.kill()
        await proc.wait()

    async def exec(self, command: str) -> dict:
        """Execute a command. Stdout streams to task log.

        Args:
            command: Shell command to execute.

        Returns:
            {exit_code, output, lines}. Output is the whole of stdout unless it
            exceeds the bridge's max_output (16MB by default), in which case the
            middle is dropped and `truncated` says how many lines went with it.
        """
        proc = await self._spawn()
        try:
            proc.stdin.write(command.encode() + b"\n")
            proc.stdin.close()

            head: list[str] = []
            tail: deque[str] = deque(maxlen=_KEEP)
            size = 0
            total = 0
            overflowed = False

            async for line in proc.stdout:
                text = line.decode(errors="replace").rstrip()
                self.log(text)
                total += 1
                size += len(text) + 1
                if overflowed:
                    tail.append(text)
                elif size > self._max_output:
                    overflowed = True
                    del head[_KEEP:]
                    tail.append(text)
                else:
                    head.append(text)

            await proc.wait()
        except (asyncio.CancelledError, Exception):
            await self._kill(proc)
            raise

        result = {"exit_code": proc.returncode, "lines": total}
        if not overflowed:
            result["output"] = "\n".join(head)
            return result

        dropped = total - len(head) - len(tail)
        result["output"] = "\n".join(
            [*head, f"... {dropped} lines omitted ({size} bytes total) ...", *tail]
        )
        result["truncated"] = dropped
        cap = self._max_output
        shown = f"{cap / 1048576:.0f}MB" if cap >= 1048576 else f"{cap / 1024:.0f}KB"
        result["note"] = (
            f"Output exceeded {shown}, so {dropped} lines from the middle were "
            f"dropped. Narrow the command, or raise the bridge's max_output. To move "
            f"a file, fetch it as one line — `base64 -w0 out.bin` "
            f"(or `base64 out.bin | tr -d '\\n'`) — and decode here."
        )
        return result

    async def shell(self, command: str | None = None) -> dict:
        """Open interactive shell session. Use task.send() for input.

        Args:
            command: Optional first command to run on start, e.g. launch a
                REPL like `claude` or `python -i`. Stdin stays open, so
                task.send() keeps feeding the running process.

        Returns:
            {exit_code} when the shell exits.
        """
        proc = await self._spawn()
        self.log(f"Shell started ({self._command})")
        if command:
            proc.stdin.write((command + "\n").encode())
            await proc.stdin.drain()

        async def reader():
            async for line in proc.stdout:
                self.log(line.decode(errors="replace").rstrip())

        async def writer():
            ctx = _current_task.get()
            if not ctx:
                return
            try:
                while True:
                    msg = await ctx.queue.get()
                    proc.stdin.write((msg + "\n").encode())
                    await proc.stdin.drain()
            except (asyncio.CancelledError, OSError):
                pass

        read_task = asyncio.create_task(reader())
        write_task = asyncio.create_task(writer())
        try:
            await proc.wait()
        except (asyncio.CancelledError, Exception):
            await self._kill(proc)
            raise
        finally:
            write_task.cancel()
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await write_task
            with contextlib.suppress(asyncio.CancelledError):
                await read_task

        return {"exit_code": proc.returncode}
