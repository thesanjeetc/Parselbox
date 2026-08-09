import pytest

from parselbox import Parselbox, Mount

pytestmark = pytest.mark.asyncio


class TestResourceLimits:
    async def test_memory_limit_enforced(self):
        async with Parselbox(memory=512) as sandbox:
            result = await sandbox.execute_code("bytearray(400 * 1024 * 1024); 'ok'")
            assert result.is_success
            assert result.output == "ok"

            result = await sandbox.execute_code("bytearray(600 * 1024 * 1024); 'ok'")
            assert result.is_success is False
            assert "MemoryError" in result.error

    async def test_memory_limit_recovery(self):
        async with Parselbox(memory=512) as sandbox:
            await sandbox.execute_code("bytearray(600 * 1024 * 1024)")
            result = await sandbox.execute_code("2 + 2")
            assert result.is_success
            assert result.output == 4

    async def test_timeout_kills_infinite_loop(self):
        async with Parselbox(timeout=3) as sandbox:
            result = await sandbox.execute_code("while True: pass")
            assert result.is_success is False
            assert "timed out" in result.error.lower()

    async def test_timeout_preserves_state(self):
        async with Parselbox(timeout=5) as sandbox:
            setup = await sandbox.execute_code("x = 42")
            assert setup.is_success, setup.error
            timed_out = await sandbox.execute_code("while True: pass")
            assert timed_out.is_success is False
            result = await sandbox.execute_code("x")
            assert result.is_success, result.error
            assert result.output == 42


class TestJsHeapRecovery:
    async def test_js_heap_oom_recovers(self):
        async with Parselbox(timeout=5) as sandbox:
            result = await sandbox.execute_code(
                'js("const a=[]; while(true) a.push(new Array(100000))")'
            )
            assert not result.is_success
            assert "restarted" in result.error.lower()

            result = await sandbox.execute_code("1 + 1")
            assert result.is_success
            assert result.output == 2


class TestRecovery:
    async def test_sandbox_recovery_after_crash(self, tmp_path):
        ro_dir = tmp_path / "readonly"
        ro_dir.mkdir()

        async with Parselbox(
            packages=["humanize"],
            mounts=[Mount(host=str(ro_dir), target="data")],
        ) as sandbox:
            await sandbox.execute_code("IMPORTANT_VAR = 999")
            result = await sandbox.execute_code(
                "import humanize; humanize.intword(1000000)"
            )
            assert result.is_success
            assert result.output == "1.0 million"

            result = await sandbox.execute_code(
                "open('/mnt/data/hack', 'w').write('fail')"
            )
            assert result.is_success is False
            assert "permissionerror" in result.error.lower()

            result = await sandbox.execute_code("IMPORTANT_VAR")
            assert result.output == 999

            result = await sandbox.execute_code(
                "import humanize; humanize.intword(1000000)"
            )
            assert result.is_success
            assert result.output == "1.0 million"
