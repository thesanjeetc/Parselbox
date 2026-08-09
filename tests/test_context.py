import pytest
from textwrap import dedent
from pydantic import BaseModel
from unittest.mock import MagicMock

from parselbox import Mount, Parselbox
from parselbox.bridge import Bridge, MCPBridge

pytestmark = pytest.mark.asyncio


class Coordinate(BaseModel):
    x: int
    y: int
    z: int


class Thrusters(Bridge):
    def fire(self, duration: float, power: int = 100):
        """Fires the main thrusters."""
        return {"status": "fired", "duration": duration, "thrust_output": power * 1.5}


class Navigation(Bridge):
    def __init__(self):
        self.thrusters = Thrusters()

    def calculate_jump(self, x: int, y: int, z: int) -> dict:
        """Calculates the hyperspace jump."""
        return {"vector": [x, y, z], "fuel_required": (x + y + z) * 0.5}

    def analyze_sector(self, coord: Coordinate) -> str:
        """Analyzes a sector."""
        return f"Sector {coord.x}:{coord.y}:{coord.z} is clear."


class Comms(Bridge):
    def broadcast(self, message: str, encrypt: bool = True) -> str:
        """Broadcasts a message."""
        prefix = "[ENCRYPTED]" if encrypt else "[OPEN]"
        return f"{prefix} Message sent: {message}"


class MissionControl(Bridge):
    def __init__(self):
        self.nav = Navigation()
        self.comms = Comms()
        self._secret_code = "12345"

    def abort(self):
        """Aborts the mission."""
        return "MISSION ABORTED"


@pytest.fixture
def mission_context():
    return {
        "mission": MissionControl(),
    }


class TestHostIntegration:
    async def test_call_root_method(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("mission.abort()")
            assert result.output == "MISSION ABORTED"

    async def test_call_nested_method(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = "mission.nav.calculate_jump(x=10, y=20, z=30)"
            result = await sandbox.execute_code(code)
            assert result.output["vector"] == [10, 20, 30]

    async def test_call_deeply_nested_method(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = "mission.nav.thrusters.fire(duration=5.0)"
            result = await sandbox.execute_code(code)
            assert result.output["status"] == "fired"

    async def test_pydantic_input_handling(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent(
                """
                coord_data = {"x": 1, "y": 2, "z": 3}
                mission.nav.analyze_sector(coord=coord_data)
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output == "Sector 1:2:3 is clear."


class TestSecurityBoundaries:
    async def test_access_denied_to_hidden_property(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent(
                """
                try:
                    mission._secret_code
                    res = "accessible"
                except Exception as e:
                    res = str(e)
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert "not found" in str(result.output) or "no attribute" in str(
                result.output
            )

    async def test_access_allowed_via_namespace_list(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("mission.nav is not None")
            assert result.output is True

    async def test_nested_private_method_blocked(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent(
                """
                try:
                    mission.nav._secret_method
                    res = "accessible"
                except Exception as e:
                    res = str(e)
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert "not found" in str(result.output) or "no attribute" in str(
                result.output
            )

    async def test_nonexistent_context_raises(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("nonexistent.method()")
            assert result.is_success is False

    async def test_unknown_op_raises(self):
        from parselbox.context import ContextManager
        from parselbox.models import Callback

        class Dummy(Bridge):
            def method(self):
                return 1

        dummy = Dummy()
        await dummy._pbx_connect()
        ctx = ContextManager({"d": dummy})
        cb = Callback(name="d", op="delete", path=[], args=[], kwargs={})
        with pytest.raises(ValueError, match="Unknown operation"):
            await ctx.execute(cb)


class TestIntrospectionToolkit:
    async def test_sbx_help(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("sbx.help()")
            assert result.is_success
            assert isinstance(result.output, str)
            assert "Parselbox" in result.output or "sandbox" in result.output.lower()

    async def test_sbx_info(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        file_a = tmp_path / "a.txt"
        file_a.write_text("A")

        async with Parselbox(
            context={"mission": MissionControl()},
            packages=["numpy"],
            network=True,
            allow_runtime_packages=True,
            mounts=[Mount(host=str(data_dir), target="mydata", mode="rw")],
            files=[str(file_a)],
        ) as sandbox:
            result = await sandbox.execute_code("sbx.info()")
            info = result.output

            assert "context" in info
            assert any("mission" in ns for ns in info["context"]["namespaces"])
            assert any("sbx" in ns for ns in info["context"]["namespaces"])

            env = info["environment"]
            assert env["network"] is True
            assert env["allow_runtime_packages"] is True
            assert "numpy" in env["packages"]

            assert info["mounts"] is not None
            assert any("mydata" in m and "read/write" in m for m in info["mounts"])

            assert info["files"] is not None
            assert "a.txt" in info["files"]["items"]

    async def test_sbx_dir(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("dir(mission)")
            assert "nav" in result.output
            assert "abort" in result.output
            assert "search" not in result.output

            result = await sandbox.execute_code("dir(mission.nav)")
            assert "calculate_jump" in result.output
            assert "thrusters" in result.output

    async def test_sbx_describe(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = (
                "sbx.inspect(['mission.nav.calculate_jump', 'mission.comms.broadcast'])"
            )
            result = await sandbox.execute_code(code)

            assert (
                "Calculates the hyperspace jump"
                in result.output["mission.nav.calculate_jump"]["description"]
            )
            assert (
                "x"
                in result.output["mission.nav.calculate_jump"]["parameters"][
                    "properties"
                ]
            )

            proxy_result = await sandbox.execute_code(
                "sbx.inspect([mission.nav.calculate_jump])"
            )
            assert (
                "Calculates"
                in proxy_result.output["mission.nav.calculate_jump"]["description"]
            )
            assert (
                "Broadcasts" in result.output["mission.comms.broadcast"]["description"]
            )

    async def test_sbx_search(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("sbx.search('fire')")
            assert result.is_success
            assert "mission (namespace)" in result.output
            assert any(
                "fire" in r["path"] for r in result.output["mission (namespace)"]
            )

            result = await sandbox.execute_code("sbx.search('jump|broadcast')")
            assert result.is_success
            assert len(result.output.get("mission (namespace)", [])) >= 2

            result = await sandbox.execute_code("sbx.search('abort')")
            assert result.is_success
            assert "mission (namespace)" in result.output

    async def test_sbx_preview(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                data = {
                    'long_list': list(range(100)),
                    'long_string': 'x' * 500,
                    'nested': {'a': [1, 2, 3, 4, 5]},
                    'empty': [],
                }
                sbx.preview(data)
            """)
            result = await sandbox.execute_code(code)
            preview = result.output

            assert len(preview["long_list"]) == 3
            assert "more item" in preview["long_list"][-1]

            assert preview["long_string"].endswith("...")
            assert len(preview["long_string"]) <= 203

            assert "more" in preview["nested"]["a"][-1]

            assert preview["empty"] == []

    async def test_sbx_preview_max_depth(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = "sbx.preview({'a': {'b': {'c': {'d': {'e': 'deep'}}}}})"
            result = await sandbox.execute_code(code)
            assert "max depth" in str(result.output).lower()


class TestErrorPropagation:
    async def test_host_function_error(self):
        class Broken(Bridge):
            def explode(self):
                raise ValueError("Kaboom!")

        async with Parselbox(context={"broken": Broken()}) as sandbox:
            result = await sandbox.execute_code("broken.explode()")
            assert result.is_success is False
            assert "Kaboom!" in result.error

    async def test_parameter_validation_error(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = "mission.nav.calculate_jump(x=10)"
            result = await sandbox.execute_code(code)

            assert result.is_success is False
            err_msg = result.error.lower()
            assert "missing" in err_msg or "validation" in err_msg


class TestSkills:
    async def test_skill_read_via_filesystem(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test-skill\ndescription: test\n---\n# Instructions\nDo stuff."
        )

        async with Parselbox(
            mounts=[Mount(host=str(tmp_path), target="skills")],
        ) as sandbox:
            result = await sandbox.execute_code(
                'open("/mnt/skills/test-skill/SKILL.md").read()'
            )
            assert result.is_success
            assert "Instructions" in result.output

    async def test_skills_hidden_from_namespaces(self, tmp_path):
        d = tmp_path / "my-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n")

        async with Parselbox(
            mounts=[Mount(host=str(tmp_path), target="skills")],
        ) as sandbox:
            result = await sandbox.execute_code("sbx.info()")
            assert result.is_success
            namespaces = result.output["context"]["namespaces"]
            assert not any("skills" in ns for ns in namespaces)


class TestMCPBridge:
    @pytest.fixture
    def mcp(self):
        proxy = MCPBridge({"mcpServers": {"gh": {"url": "http://x"}}})
        tool = MagicMock()
        tool.name = "search"
        tool.description = "Search repos"
        tool.inputSchema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool.outputSchema = None
        proxy.tools = {"search": tool}
        proxy._pbx_tool_index = {
            "search": {
                "leaf": "search",
                "desc": "Search repos",
                "params": ["q"],
                "fields": [],
            }
        }
        proxy.client = MagicMock()
        proxy.client.is_connected.return_value = True
        return proxy

    async def test_dir(self, mcp):
        async with Parselbox(context={"gh": mcp}) as sandbox:
            result = await sandbox.execute_code("dir(gh)")
            assert "search" in result.output

    async def test_describe_returns_schema(self, mcp):
        async with Parselbox(context={"gh": mcp}) as sandbox:
            result = await sandbox.execute_code("sbx.inspect(['gh.search'])")
            assert "Search repos" in result.output["gh.search"]["description"]
            assert "q" in result.output["gh.search"]["parameters"]["properties"]


class TestTaskProxy:
    async def test_task_basic(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("t = mission.abort.task(); await t")
            assert result.output == "MISSION ABORTED"

    async def test_task_nested(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code(
                "t = mission.nav.calculate_jump.task(x=10, y=20, z=30); await t"
            )
            assert result.output["vector"] == [10, 20, 30]

    async def test_task_gather(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                import asyncio
                t1 = mission.abort.task()
                t2 = mission.nav.calculate_jump.task(x=1, y=2, z=3)
                results = await asyncio.gather(t1, t2)
                results
            """)
            result = await sandbox.execute_code(code)
            assert result.output[0] == "MISSION ABORTED"
            assert result.output[1]["vector"] == [1, 2, 3]

    async def test_task_status(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                task = mission.abort.task()
                s = await task.wait()
                {"state": s.state, "ok": s.ok, "result": s.result}
            """)
            result = await sandbox.execute_code(code)
            assert result.output["state"] == "done"
            assert result.output["ok"] is True
            assert result.output["result"] == "MISSION ABORTED"


class TestAutoWrap:
    async def test_plain_class_auto_wrapped(self):
        class Sensor:
            def temperature(self) -> float:
                return 23.5

            def pressure(self) -> float:
                return 1013.0

        async with Parselbox(context={"sensor": Sensor()}) as sandbox:
            result = await sandbox.execute_code("sensor.temperature()")
            assert result.output == 23.5

            result = await sandbox.execute_code("sensor.pressure()")
            assert result.output == 1013.0

            result = await sandbox.execute_code("dir(sensor)")
            assert "temperature" in result.output
            assert "pressure" in result.output

            result = await sandbox.execute_code("sbx.search('temperature')")
            assert result.is_success
            assert any("temperature" in str(v) for v in result.output.values())


class TestFunctionBridge:
    async def test_sync_function(self):
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        async with Parselbox(context={"add": add}) as sandbox:
            result = await sandbox.execute_code("add(3, 4)")
            assert result.output == 7

    async def test_sync_function_kwargs(self):
        def greet(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        async with Parselbox(context={"greet": greet}) as sandbox:
            result = await sandbox.execute_code("greet('Alice')")
            assert result.output == "Hello, Alice!"

            result = await sandbox.execute_code("greet('Bob', greeting='Hi')")
            assert result.output == "Hi, Bob!"

    async def test_async_function(self):
        async def fetch_data(key: str) -> dict:
            return {"key": key, "value": 42}

        async with Parselbox(context={"fetch_data": fetch_data}) as sandbox:
            result = await sandbox.execute_code("fetch_data(key='test')")
            assert result.output == {"key": "test", "value": 42}

    async def test_function_as_task(self):
        def slow_add(a: int, b: int) -> int:
            import time

            time.sleep(0.1)
            return a + b

        async with Parselbox(context={"slow_add": slow_add}) as sandbox:
            code = dedent("""
                task = slow_add.task(10, 20)
                await task.wait(3)
                task.result()
            """)
            result = await sandbox.execute_code(code)
            assert result.output == 30

    async def test_function_error_propagation(self):
        def explode(x: int) -> int:
            raise ValueError(f"Bad value: {x}")

        async with Parselbox(context={"explode": explode}) as sandbox:
            result = await sandbox.execute_code("explode(x=42)")
            assert result.is_success is False
            assert "Bad value: 42" in result.error

    async def test_function_alongside_bridge(self):
        def multiply(a: int, b: int) -> int:
            return a * b

        class Sensor(Bridge):
            def read(self) -> float:
                return 25.0

        async with Parselbox(
            context={"multiply": multiply, "sensor": Sensor()}
        ) as sandbox:
            code = "multiply(int(sensor.read()), 2)"
            result = await sandbox.execute_code(code)
            assert result.output == 50

    async def test_lambda_raises(self):
        with pytest.raises(ValueError, match="name"):
            Parselbox(context={"double": lambda x: x * 2})


class TestFireAndForget:
    async def test_task_does_not_block(self):
        import time

        class Slow(Bridge):
            async def slow_work(self) -> str:
                import asyncio

                await asyncio.sleep(2)
                return "done"

        async with Parselbox(context={"slow": Slow()}) as sandbox:
            t0 = time.perf_counter()
            result = await sandbox.execute_code("t = slow.slow_work.task()")
            elapsed = (time.perf_counter() - t0) * 1000
            assert result.output is None
            assert elapsed < 500, f"Should not block, took {elapsed:.0f}ms"

            result = await sandbox.execute_code("await t")
            assert result.output == "done"


class TestTaskAPI:
    """Comprehensive tests for the task system: status, wait, log, send, cancel."""

    async def test_status_done(self):
        class Worker(Bridge):
            async def work(self):
                return {"result": 42, "status": "ok"}

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.work.task()
                await asyncio.sleep(0.5)
                s = task.status()
                {"state": s.state, "ok": s.ok, "result": s.result}
            """)
            )
            assert result.output["state"] == "done"
            assert result.output["ok"] is True
            assert result.output["result"] == {"result": 42, "status": "ok"}

    async def test_status_failed(self):
        class Worker(Bridge):
            async def fail(self):
                raise ValueError("boom")

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.fail.task()
                await asyncio.sleep(0.5)
                s = task.status()
                {"state": s.state, "ok": s.ok, "error": s.error}
            """)
            )
            assert result.output["state"] == "failed"
            assert result.output["ok"] is False
            assert "boom" in result.output["error"]

    async def test_wait_timeout_returns_status(self):
        class Worker(Bridge):
            async def slow(self):
                import asyncio

                await asyncio.sleep(10)

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                task = w.slow.task()
                s = await task.wait(timeout=0.5)
                {"state": s.state, "done": s.done}
            """)
            )
            assert result.output["state"] == "running"
            assert result.output["done"] is False

    async def test_wait_done_returns_result(self):
        class Worker(Bridge):
            async def fast(self):
                return "quick"

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                task = w.fast.task()
                s = await task.wait()
                {"state": s.state, "result": s.result}
            """)
            )
            assert result.output["state"] == "done"
            assert result.output["result"] == "quick"

    async def test_logfile_and_tail(self):
        class Worker(Bridge):
            async def logged(self):
                import asyncio

                for i in range(5):
                    self.log(f"step {i}")
                    await asyncio.sleep(0.1)
                return "done"

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.logged.task()
                await asyncio.sleep(1)
                tail = task.tail(3)
                logfile = task.logfile
                lines = open(logfile).readlines()
                {"tail": tail, "logfile_exists": len(lines) > 0, "total_lines": len(lines)}
            """)
            )
            assert result.output["logfile_exists"] is True
            assert result.output["total_lines"] == 5
            assert "step" in result.output["tail"]

    async def test_send_recv(self):
        class Worker(Bridge):
            async def echo(self):
                import asyncio

                while True:
                    for msg in self.recv():
                        self.log(f"got: {msg}")
                        if msg == "quit":
                            return "done"
                    await asyncio.sleep(0.1)

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.echo.task()
                await asyncio.sleep(0.3)
                task.send("hello")
                await asyncio.sleep(0.3)
                task.send("quit")
                s = await task.wait(timeout=3)
                {"state": s.state, "result": s.result, "tail": task.tail(5)}
            """)
            )
            assert result.output["state"] == "done"
            assert result.output["result"] == "done"
            assert "got: hello" in result.output["tail"]

    async def test_cancel(self):
        class Worker(Bridge):
            async def forever(self):
                import asyncio

                while True:
                    await asyncio.sleep(0.1)

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.forever.task()
                await asyncio.sleep(0.3)
                task.cancel()
                await asyncio.sleep(0.3)
                s = task.status()
                s.state
            """)
            )
            assert result.output == "cancelled"

    async def test_result_and_done(self):
        class Worker(Bridge):
            async def quick(self):
                return 99

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = w.quick.task()
                before_done = task.done()
                before_result = task.result()
                await asyncio.sleep(0.5)
                after_done = task.done()
                after_result = task.result()
                {"before": [before_done, before_result], "after": [after_done, after_result]}
            """)
            )
            assert result.output["after"] == [True, 99]

    async def test_task_gather(self):
        class Worker(Bridge):
            async def add(self, a: int, b: int):
                return a + b

        async with Parselbox(context={"w": Worker()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                results = await asyncio.gather(
                    w.add.task(a=1, b=2),
                    w.add.task(a=10, b=20),
                    w.add.task(a=100, b=200),
                )
                results
            """)
            )
            assert result.output == [3, 30, 300]
