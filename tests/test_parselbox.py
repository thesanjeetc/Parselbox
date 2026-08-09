import asyncio
from textwrap import dedent

import pytest

from parselbox import Parselbox

pytestmark = pytest.mark.asyncio


class TestCoreExecution:
    async def test_basic_execution(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("x = 10 + 32; x")
            assert result.output == 42

    async def test_globals_injection(self):
        params = {"user_name": "Alice", "score": 100}
        async with Parselbox(globals=params) as sandbox:
            result = await sandbox.execute_code("f'{user_name} has {score} points'")
            assert result.output == "Alice has 100 points"

    async def test_state_persistence(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("x = 500")
            result = await sandbox.execute_code("x + 1")
            assert result.output == 501

    async def test_syntax_error(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("def broken_code(")
            assert result.is_success is False
            assert "SyntaxError" in result.error

    async def test_return_complex_types(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                data = {
                    "list": [1, 2, 3],
                    "dict": {"a": 1, "b": 2},
                    "bool": True,
                    "none": None
                }
                data
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output.get("list") == [1, 2, 3]
            assert result.output.get("dict") == {"a": 1, "b": 2}
            assert result.output.get("bool") is True
            assert result.output.get("none") is None

    async def test_output_types(self):
        async with Parselbox() as sandbox:
            assert (await sandbox.execute_code("42")).output == 42
            assert (await sandbox.execute_code('"hello"')).output == "hello"
            assert (await sandbox.execute_code("[1, 2, 3]")).output == [1, 2, 3]
            assert (await sandbox.execute_code('{"a": 1}')).output == {"a": 1}
            assert (await sandbox.execute_code("True")).output is True
            assert (await sandbox.execute_code("None")).output is None

    async def test_error_propagation(self):
        async with Parselbox() as sandbox:
            r = await sandbox.execute_code("1/0")
            assert not r.is_success
            assert "ZeroDivisionError" in r.error


class TestStdoutStderr:
    async def test_stdout_capture(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                print("Hello")
                print("World")
                42
            """)
            )
            assert result.is_success
            assert result.output == 42
            assert result.stdout == "Hello\nWorld\n"

    async def test_stderr_capture(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import sys
                print("error message", file=sys.stderr)
                "done"
            """)
            )
            assert result.is_success
            assert result.output == "done"
            assert result.stderr == "error message\n"

    async def test_stdout_on_error(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                print("before")
                raise ValueError("oops")
            """)
            )
            assert result.is_success is False
            assert "ValueError" in result.error
            assert result.stdout == "before\n"

    async def test_stdout_cleared_between_executions(self):
        async with Parselbox() as sandbox:
            result1 = await sandbox.execute_code('print("first")')
            result2 = await sandbox.execute_code('print("second")')
            assert result1.stdout == "first\n"
            assert result2.stdout == "second\n"

    async def test_no_stdout_returns_none_or_empty(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("1 + 1")
            assert result.stdout is None


class TestSerialization:
    async def test_shadowed_builtins_do_not_break_results(self):
        async with Parselbox() as sandbox:
            for name in ("id", "str", "isinstance", "hasattr", "repr", "type"):
                result = await sandbox.execute_code(f'{name} = 5\n{{"a": 1}}')
                assert result.is_success, f"shadowing {name!r} broke serialization"
                assert result.output == {"a": 1}

    async def test_serialization_sets_and_tuples(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                s = {1, 2, 3}
                t = (4, 5, 6)
                {"set": s, "tuple": t}
            """
            )
            result = await sandbox.execute_code(code)
            assert sorted(result.output["set"]) == [1, 2, 3]
            assert result.output["tuple"] == [4, 5, 6]

    async def test_serialization_datetime(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                import datetime
                d = datetime.date(2023, 10, 27)
                t = datetime.datetime(2023, 10, 27, 12, 0, 0)
                {"date": d, "time": t}
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output["date"] == "2023-10-27"
            assert "2023-10-27" in result.output["time"]

    async def test_serialization_circular_reference(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                a = {"name": "A"}
                b = {"name": "B", "parent": a}
                a["child"] = b
                a
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output["name"] == "A"
            assert result.output["child"]["name"] == "B"
            cycle = result.output["child"]["parent"]
            assert cycle["type"] == "circular_reference"
            assert "'name': 'A'" in cycle["repr"]

    async def test_serialization_custom_object(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                class User:
                    def __init__(self, id):
                        self.id = id
                    def __repr__(self):
                        return f"<User id={self.id}>"
                User(123)
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output["type"] == "not_serializable"
            assert result.output["repr"] == "<User id=123>"

    async def test_nan_serializes_as_null(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("float('nan')")
            assert result.output is None

    async def test_infinity_serializes_as_null(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("float('inf')")
            assert result.output is None


class TestCrashRecovery:
    async def test_process_crash_recovers(self):
        async with Parselbox(timeout=5) as sandbox:

            async def kill_soon():
                await asyncio.sleep(0.1)
                sandbox._proc.kill()

            asyncio.create_task(kill_soon())

            result = await sandbox.execute_code("import time; time.sleep(5)")
            assert not result.is_success
            assert "restarted" in result.error.lower()

            result = await sandbox.execute_code("1 + 1")
            assert result.is_success
            assert result.output == 2


class TestBashBuiltin:
    async def test_bash_returns_stdout(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code('bash("echo hello")')
            assert result.is_success
            assert "hello" in result.output

    async def test_bash_nonzero_exit_with_stderr_raises(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                try:
                    bash("echo fail >&2; exit 1")
                    res = "no error"
                except RuntimeError as e:
                    res = str(e)
                res
            """)
            )
            assert result.is_success
            assert "exit 1" in result.output
            assert "fail" in result.output

    async def test_bash_pipe(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code(
                """bash("echo -e 'c\\na\\nb' | sort")"""
            )
            assert result.is_success
            lines = result.output.strip().split("\n")
            assert lines == ["a", "b", "c"]

    async def test_bash_and_python_share_filesystem(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code('bash("echo from_bash > shared.txt")')
            result = await sandbox.execute_code('open("shared.txt").read().strip()')
            assert result.output == "from_bash"

            await sandbox.execute_code("open('from_py.txt', 'w').write('from_python')")
            result = await sandbox.execute_code('bash("cat from_py.txt")')
            assert "from_python" in result.output


class TestMCPConfig:
    async def test_invalid_mcp_config_raises(self):
        with pytest.raises(ValueError, match="mcpServers"):
            Parselbox(mcp={"bad": "config"})
