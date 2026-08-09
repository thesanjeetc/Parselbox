"""Tests for WASI command modules via require()."""

import base64
from textwrap import dedent

import pytest

from parselbox import Parselbox

pytestmark = pytest.mark.asyncio

ADD_WASM = base64.b64decode("AGFzbQEAAAABBwFgAn9/AX8DAgEABwcBA2FkZAAACgkBBwAgACABags=")
HELLO_WASM = base64.b64decode(
    "AGFzbQEAAAABDAJgBH9/f38Bf2AAAAIjARZ3YXNpX3NuYXBzaG90X3ByZXZpZXcxCGZkX3dyaXRl"
    "AAADAgEBBQMBAAEHEwIGbWVtb3J5AgAGX3N0YXJ0AAEKHQEbAEEAQQg2AgBBBEEVNgIAQQFBAEEB"
    "QRwQABoLCxsBAEEICxVoZWxsbyBmcm9tIFdBU0kgbGFuZAo="
)
WEIRD_IMPORT_WASM = base64.b64decode(
    "AGFzbQEAAAABCgJgAX8Bf2AAAX8CFAEDZW52DHNvbWVfaG9zdF9mbgAAAwIBAQcGAQJnbwABCggB"
    "BgBBARAACw=="
)
WRITER_WASM = base64.b64decode(
    "AGFzbQEAAAABGQNgCX9/f39/fn5/fwF/YAR/f39/AX9gAAACRgIWd2FzaV9zbmFwc2hvdF9wcmV2"
    "aWV3MQlwYXRoX29wZW4AABZ3YXNpX3NuYXBzaG90X3ByZXZpZXcxCGZkX3dyaXRlAAEDAgECBQMB"
    "AAEHEwIGbWVtb3J5AgAGX3N0YXJ0AAIKNwE1AEEDQQBB5ABBB0EJQn9Cf0EAQQgQABpBAEHIATYC"
    "AEEEQQ82AgBBCCgCAEEAQQFBEBABGgsLIwIAQeQACwdvdXQudHh0AEHIAQsPd2FzaSB3cm90ZSB0"
    "aGlz"
)


async def run(sbx, code):
    result = await sbx.execute_code(dedent(code).strip())
    if not result.is_success:
        raise AssertionError(f"Execution failed: {result.error}")
    return result


class TestWasi:
    async def test_self_contained_exports(self):
        async with Parselbox() as sbx:
            sbx.write_file("add.wasm", ADD_WASM)
            r = await run(sbx, 'require("./add.wasm").add(20, 22)')
            assert r.output == 42

    async def test_wasi_command_runner(self):
        async with Parselbox() as sbx:
            sbx.write_file("hello.wasm", HELLO_WASM)
            r = await run(
                sbx,
                """
                hello = require("./hello.wasm")
                r = hello()
                (r["exit"], r["stdout"].decode(), r["stderr"], r["missing"])
                """,
            )
            assert r.output == [0, "hello from WASI land\n", "", []]

    async def test_runner_has_docs(self):
        async with Parselbox() as sbx:
            sbx.write_file("hello.wasm", HELLO_WASM)
            r = await run(sbx, 'require("./hello.wasm").__doc__')
            assert r.output.startswith("WASI command")

    async def test_wasi_write_path(self):
        async with Parselbox() as sbx:
            sbx.write_file("writer.wasm", WRITER_WASM)
            r = await run(
                sbx,
                """
                writer = require("./writer.wasm")
                writer()
                open("out.txt").read()
                """,
            )
            assert r.output == "wasi wrote this"
            assert sbx.read_file("out.txt") == "wasi wrote this"

    async def test_wasi_result_files_detection(self):
        async with Parselbox() as sbx:
            sbx.write_file("writer.wasm", WRITER_WASM)
            r = await run(sbx, 'require("./writer.wasm")()["exit"]')
            assert r.output == 0
            assert any(f.endswith("out.txt") for f in r.files)

    async def test_argv0_override(self):
        async with Parselbox() as sbx:
            sbx.write_file("hello.wasm", HELLO_WASM)
            r = await run(
                sbx,
                """
                hello = require("./hello.wasm")
                hello(argv0="wasm-ld")["exit"]
                """,
            )
            assert r.output == 0

    async def test_bash_dispatches_to_wasm_on_path(self):
        async with Parselbox() as sbx:
            sbx.write_file("bin/greet.wasm", HELLO_WASM)
            r = await run(sbx, 'bash("greet | tr a-z A-Z")')
            assert r.output.strip() == "HELLO FROM WASI LAND"

    async def test_bash_wasm_discovered_mid_session(self):
        async with Parselbox() as sbx:
            r = await sbx.execute_code('bash("greet")')
            assert "greet" not in (r.output or "")
            r = await run(
                sbx,
                """
                import os
                os.makedirs("bin", exist_ok=True)
                open("bin/greet.wasm", "wb").write(data)
                bash("greet")
                """.replace("data", repr(HELLO_WASM)),
            )
            assert r.output.strip() == "hello from WASI land"

    async def test_unknown_import_namespace_errors(self):
        async with Parselbox() as sbx:
            sbx.write_file("weird.wasm", WEIRD_IMPORT_WASM)
            r = await sbx.execute_code('require("./weird.wasm")')
            assert not r.is_success
            assert "env" in r.error and "js()" in r.error
