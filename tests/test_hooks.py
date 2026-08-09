import pytest

from parselbox import Parselbox, Hook
from parselbox.bridge import Bridge

pytestmark = pytest.mark.asyncio


class RecordingHook(Hook):
    def __init__(self):
        self.events = []

    async def pre_execute(self, code):
        self.events.append(("pre_execute", code))

    async def post_execute(self, result):
        self.events.append(("post_execute", result))

    async def pre_tool_call(self, callback):
        self.events.append(("pre_tool_call", callback))

    async def post_tool_call(self, callback, result):
        self.events.append(("post_tool_call", callback, result))


class BlockingHook(Hook):
    async def pre_execute(self, code):
        if "blocked" in code:
            raise PermissionError("Code contains blocked keyword")


class MathBridge(Bridge):
    def add(self, a: int, b: int) -> int:
        """Add two numbers."""
        return a + b


class TestHooks:
    async def test_hooks_fire_on_execute(self):
        hook = RecordingHook()
        async with Parselbox(hooks=[hook]) as sandbox:
            await sandbox.execute_code("1 + 1")

        assert len(hook.events) == 2
        assert hook.events[0][0] == "pre_execute"
        assert hook.events[0][1] == "1 + 1"
        assert hook.events[1][0] == "post_execute"
        assert hook.events[1][1].output == 2

    async def test_hooks_fire_on_tool_call(self):
        hook = RecordingHook()

        async with Parselbox(context={"math": MathBridge()}, hooks=[hook]) as sandbox:
            await sandbox.execute_code("math.add(a=1, b=2)")

        tool_events = [e for e in hook.events if "tool_call" in e[0]]
        assert len(tool_events) == 2
        assert tool_events[0][0] == "pre_tool_call"
        assert tool_events[1][0] == "post_tool_call"

    async def test_blocking_hook_denies_execution(self):
        hook = BlockingHook()
        async with Parselbox(hooks=[hook]) as sandbox:
            result = await sandbox.execute_code("x = 'blocked'")
            assert not result.is_success
            assert "Blocked by hook" in result.error

            result = await sandbox.execute_code("1 + 1")
            assert result.is_success
            assert result.output == 2

    async def test_multiple_hooks_run_in_order(self):
        hook1 = RecordingHook()
        hook2 = RecordingHook()
        async with Parselbox(hooks=[hook1, hook2]) as sandbox:
            await sandbox.execute_code("1 + 1")

        assert len(hook1.events) == 2
        assert len(hook2.events) == 2
