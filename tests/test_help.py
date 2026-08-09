import pytest
from textwrap import dedent

from parselbox import Parselbox
from parselbox.bridge import Bridge
from parselbox.context import ContextManager
from parselbox.models import Callback

pytestmark = pytest.mark.asyncio


class Navigation(Bridge):
    def calculate_jump(self, x: int, y: int, z: int) -> dict:
        """Calculates the hyperspace jump vector."""
        return {"vector": [x, y, z]}

    def scan(self, sector: str = "alpha") -> str:
        """Scans a sector for anomalies."""
        return f"scanning {sector}"


class Comms(Bridge):
    def broadcast(self, message: str, encrypt: bool = True) -> str:
        """Broadcasts a message to all ships."""
        return f"sent: {message}"


class MissionControl(Bridge):
    def __init__(self):
        self.nav = Navigation()
        self.comms = Comms()

    def abort(self):
        """Aborts the mission immediately."""
        return "ABORTED"


class EmptyBridge(Bridge):
    pass


@pytest.fixture
def mission_context():
    return {"mission": MissionControl()}


@pytest.fixture
def multi_context():
    return {
        "mission": MissionControl(),
        "empty": EmptyBridge(),
    }


class TestBridgeHelp:
    async def test_help_on_tool(self):
        mc = MissionControl()
        await mc._pbx_connect()
        doc = mc._pbx_help("nav.calculate_jump")
        assert isinstance(doc, dict)
        assert "Calculates the hyperspace jump" in doc["description"]
        assert "x" in doc["parameters"]["properties"]

    async def test_help_on_namespace_root(self):
        mc = MissionControl()
        await mc._pbx_connect()
        doc = mc._pbx_help("")
        assert "abort()" in doc
        assert "nav" in doc
        assert "comms" in doc
        assert "├──" in doc or "└──" in doc

    async def test_help_on_sub_namespace(self):
        mc = MissionControl()
        await mc._pbx_connect()
        doc = mc._pbx_help("nav")
        assert "calculate_jump()" in doc
        assert "scan()" in doc

    async def test_help_shows_nested_tree(self):
        mc = MissionControl()
        await mc._pbx_connect()
        doc = mc._pbx_help("")
        assert "│" in doc
        assert "broadcast()" in doc

    async def test_help_on_missing_tool(self):
        mc = MissionControl()
        await mc._pbx_connect()
        doc = mc._pbx_help("nonexistent")
        assert "No documentation" in doc

    async def test_help_on_empty_bridge(self):
        eb = EmptyBridge()
        await eb._pbx_connect()
        doc = eb._pbx_help("")
        assert "No documentation" in doc


class TestContextManagerHelp:
    async def test_help_op_root(self):
        mc = MissionControl()
        await mc._pbx_connect()
        ctx = ContextManager({"mission": mc})
        cb = Callback(name="mission", op="help", path=[], args=[], kwargs={})
        result = await ctx.execute(cb)
        assert isinstance(result, str)
        assert "abort" in result
        assert "nav" in result

    async def test_help_op_tool(self):
        mc = MissionControl()
        await mc._pbx_connect()
        ctx = ContextManager({"mission": mc})
        cb = Callback(
            name="mission",
            op="help",
            path=["nav", "calculate_jump"],
            args=[],
            kwargs={},
        )
        result = await ctx.execute(cb)
        assert isinstance(result, dict)
        assert "Calculates the hyperspace jump" in result["description"]

    async def test_help_op_sub_namespace(self):
        mc = MissionControl()
        await mc._pbx_connect()
        ctx = ContextManager({"mission": mc})
        cb = Callback(name="mission", op="help", path=["nav"], args=[], kwargs={})
        result = await ctx.execute(cb)
        assert "calculate_jump" in result
        assert "scan" in result

    async def test_help_op_nonexistent_context(self):
        mc = MissionControl()
        await mc._pbx_connect()
        ctx = ContextManager({"mission": mc})
        cb = Callback(name="nope", op="help", path=[], args=[], kwargs={})
        with pytest.raises(AttributeError, match="not found"):
            await ctx.execute(cb)

    async def test_help_op_private_path_blocked(self):
        mc = MissionControl()
        await mc._pbx_connect()
        ctx = ContextManager({"mission": mc})
        cb = Callback(name="mission", op="help", path=["_secret"], args=[], kwargs={})
        with pytest.raises(AttributeError, match="not found"):
            await ctx.execute(cb)


class TestHelpIntegration:
    async def test_help_no_args_returns_sandbox_guide(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help()")
            assert result.is_success
            assert isinstance(result.output, str)

    async def test_help_on_remote_namespace(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(mission)")
            assert result.is_success
            assert "nav" in result.output
            assert "comms" in result.output
            assert "abort" in result.output

    async def test_help_on_remote_tool(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(mission.nav.calculate_jump)")
            assert result.is_success
            assert isinstance(result.output, dict)
            assert "Calculates the hyperspace jump" in result.output["description"]
            assert "x" in result.output["parameters"]["properties"]

    async def test_help_on_nested_namespace(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(mission.nav)")
            assert result.is_success
            assert "calculate_jump" in result.output
            assert "scan" in result.output

    async def test_dunder_doc_on_namespace(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("mission.__doc__")
            assert result.is_success
            assert isinstance(result.output, str)
            assert "nav" in result.output

    async def test_dunder_doc_on_tool(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("mission.comms.broadcast.__doc__")
            assert result.is_success
            assert isinstance(result.output, dict)
            assert "Broadcasts a message" in result.output["description"]

    async def test_help_returns_string_not_none(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("type(help(mission)).__name__")
            assert result.is_success
            assert result.output == "str"

    async def test_help_on_local_builtin(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(len)")
            assert result.is_success
            assert result.output is not None
            assert "number" in result.output.lower() or "items" in result.output.lower()

    async def test_help_on_local_function(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                def my_func(x: int) -> str:
                    '''Doubles a number and returns as string.'''
                    return str(x * 2)
                help(my_func)
            """)
            result = await sandbox.execute_code(code)
            assert result.is_success
            assert "Doubles a number" in result.output

    async def test_help_on_local_class(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                class Foo:
                    '''A test class.'''
                    pass
                help(Foo)
            """)
            result = await sandbox.execute_code(code)
            assert result.is_success
            assert "A test class" in result.output

    async def test_help_on_object_without_doc(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(42)")
            assert result.is_success

    async def test_help_on_sbx_namespace(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(sbx)")
            assert result.is_success
            assert isinstance(result.output, str)
            assert "inspect" in result.output or "search" in result.output

    async def test_help_composable_in_expression(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                doc = help(mission.nav.calculate_jump)
                'jump' in doc['description'].lower()
            """)
            result = await sandbox.execute_code(code)
            assert result.is_success
            assert result.output is True

    async def test_doc_on_nonexistent_tool(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code(
                "mission.nav.nonexistent_method.__doc__"
            )
            assert result.is_success
            assert "No documentation" in result.output


class TestBridgeSubclassDoc:
    async def test_base_bridge_with_custom_doc(self):
        class Documented(Bridge):
            """Custom bridge documentation."""

            def ping(self):
                """Returns pong."""
                return "pong"

        d = Documented()
        await d._pbx_connect()
        doc = d._pbx_help("")
        assert "Custom bridge documentation" in doc
        assert "ping" in doc

    async def test_wrapped_function_help(self):
        def my_tool(name: str, count: int = 1) -> str:
            """Greets someone count times."""
            return f"hello {name}" * count

        b = Bridge.wrap(my_tool)
        await b._pbx_connect()
        doc = b._pbx_help("")
        assert "my_tool()" in doc

    async def test_wrapped_object_help(self):
        class Api:
            def fetch(self, url: str) -> dict:
                """Fetches a URL."""
                return {}

            def post(self, url: str, data: dict) -> dict:
                """Posts data to a URL."""
                return {}

        b = Bridge.wrap(Api())
        await b._pbx_connect()
        doc = b._pbx_help("")
        assert "fetch()" in doc
        assert "post()" in doc

        fetch_doc = b._pbx_help("fetch")
        assert isinstance(fetch_doc, dict)
        assert "Fetches a URL" in fetch_doc["description"]


class TestHelpEdgeCases:
    async def test_single_function_context(self):
        def greet(name: str) -> str:
            """Greets someone."""
            return f"hi {name}"

        async with Parselbox(context={"greet": greet}) as sandbox:
            result = await sandbox.execute_code("help(greet)")
            assert result.is_success
            assert isinstance(result.output, str)
            assert "greet" in result.output.lower() or "Methods" in result.output

    async def test_single_function_leaf_call(self):
        def add(a: int, b: int) -> int:
            """Adds two numbers."""
            return a + b

        async with Parselbox(context={"add": add}) as sandbox:
            result = await sandbox.execute_code("add(2, 3)")
            assert result.output == 5

    async def test_auto_wrapped_class(self):
        class Calculator:
            def add(self, a: int, b: int) -> int:
                """Adds two numbers."""
                return a + b

            def multiply(self, a: int, b: int) -> int:
                """Multiplies two numbers."""
                return a * b

        async with Parselbox(context={"calc": Calculator()}) as sandbox:
            result = await sandbox.execute_code("help(calc)")
            assert result.is_success
            assert "add" in result.output
            assert "multiply" in result.output

    async def test_auto_wrapped_leaf_returns_dict(self):
        class Calculator:
            def add(self, a: int, b: int) -> int:
                """Adds two numbers."""
                return a + b

        async with Parselbox(context={"calc": Calculator()}) as sandbox:
            result = await sandbox.execute_code("help(calc.add)")
            assert result.is_success
            assert isinstance(result.output, dict)
            assert "Adds two numbers" in result.output["description"]

    async def test_help_on_task_object(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                t = mission.abort.task()
                import asyncio
                await asyncio.sleep(0.5)
                help(t)
            """)
            result = await sandbox.execute_code(code)
            assert result.is_success
            assert "status()" in result.output
            assert "wait" in result.output
            assert "tail" in result.output

    async def test_help_on_local_module(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("import json; help(json.dumps)")
            assert result.is_success
            assert result.output is not None

    async def test_help_on_none(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(None)")
            assert result.is_success

    async def test_help_inspect_same_result(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            code = dedent("""
                h = help(mission.nav.calculate_jump)
                i = sbx.inspect(["mission.nav.calculate_jump"])
                h == i["mission.nav.calculate_jump"]
            """)
            result = await sandbox.execute_code(code)
            assert result.is_success
            assert result.output is True

    async def test_namespace_hint_includes_name(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(mission)")
            assert "mission.method(args)" in result.output
            assert "mission.method.task(args)" in result.output

    async def test_nested_namespace_hint_includes_full_path(self, mission_context):
        async with Parselbox(context=mission_context) as sandbox:
            result = await sandbox.execute_code("help(mission.nav)")
            assert "mission.nav.method(args)" in result.output
