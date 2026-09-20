import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from parselbox import Mount, Parselbox
from parselbox.bridge import ShellBridge
from parselbox.models import SandboxError
from parselbox.prompt import PARSELBOX_PROMPT, PARSELBOX_UI_PROMPT
from parselbox.main import DENO_SCRIPT_PATH


async def test_cli_stdio_with_host_mount_and_unicode(tmp_path):
    source = tmp_path / "source with spaces"
    source.mkdir()
    (source / "input.txt").write_text("hello", encoding="utf-8")
    output = tmp_path / "output"
    config = {
        "mcpServers": {
            "parselbox": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "parselbox.cli",
                    "--mount",
                    f"{source}:/data:ro",
                    "--output-dir",
                    str(output),
                ],
            }
        }
    }
    async with Client(config, init_timeout=45) as client:
        assert (
            client.initialize_result.instructions
            == PARSELBOX_PROMPT + PARSELBOX_UI_PROMPT
        )
        result = await client.call_tool(
            "execute_code",
            {
                "code": "open('out.txt', 'w').write('日本語'); open('/mnt/data/input.txt').read()"
            },
        )
        assert result.data["result"] == "hello"
        result = await client.call_tool("execute_code", {"code": "1 + 1"})
        assert result.data["result"] == 2
    assert (output / "out.txt").read_text(encoding="utf-8") == "日本語"


async def test_shell_cancellation_terminates_process(monkeypatch):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=sys.platform != "win32",
    )
    bridge = ShellBridge()
    monkeypatch.setattr(bridge, "_spawn", AsyncMock(return_value=process))
    task = asyncio.create_task(bridge.exec(""))
    try:
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert process.returncode is not None
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows executable search order")
async def test_shell_uses_first_executable_on_path(tmp_path, monkeypatch):
    preferred = tmp_path / "preferred shell"
    preferred.mkdir()
    executable_name = os.path.basename(sys.executable)
    shutil.copyfile(os.environ["COMSPEC"], preferred / executable_name)
    monkeypatch.setenv("PATH", str(preferred) + os.pathsep + os.environ["PATH"])
    bridge = ShellBridge(executable_name)
    result = await bridge.exec("exit 23")
    assert result["exit_code"] == 23


def test_host_text_preserves_utf8_and_newlines(tmp_path):
    sandbox = Parselbox(output_dir=str(tmp_path))
    try:
        sandbox.write_file("text.txt", "日本語\nhello\n")
        assert (tmp_path / "text.txt").read_bytes() == "日本語\nhello\n".encode()
    finally:
        sandbox.cache_dir.cleanup()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path separators")
async def test_windows_paths_cannot_escape_mount(tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    (tmp_path / "private.txt").write_text("secret", encoding="utf-8")
    async with Parselbox(
        mounts=[Mount(str(mount), "data")], output_dir=str(tmp_path), timeout=5
    ) as sandbox:
        with pytest.raises(SandboxError):
            sandbox.resolve_path(r"/mnt/data/..\private.txt")
        result = await sandbox.execute_code(
            r"""bash("cat '/mnt/data/..\\private.txt'")"""
        )
        assert not result.is_success
        assert "secret" not in (result.output or "")


def _link_directory(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            # Directory junctions do not require Developer Mode or symlink privilege.
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=True,
                capture_output=True,
            )
        else:
            raise


async def test_temporary_directory_alias_shares_files(tmp_path, monkeypatch):
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    _link_directory(alias, actual)
    # macOS's default /var temporary directory resolves under /private/var.
    monkeypatch.setattr(tempfile, "tempdir", str(alias))
    async with Parselbox() as sandbox:
        result = await sandbox.execute_code(
            "open('shared.txt', 'w').write('from_python'); bash('cat shared.txt')"
        )
        assert result.is_success, result.error
        assert result.output == "from_python"
        result = await sandbox.execute_code(
            "bash('echo from_bash > shared.txt'); open('shared.txt').read().strip()"
        )
        assert result.is_success, result.error
        assert result.output == "from_bash"


async def test_bash_cache_updates_without_filesystem_events(tmp_path, monkeypatch):
    entry = tmp_path / "without-watcher.ts"
    entry.write_text(
        "Deno.watchFs = () => { throw new Error('watch events unavailable'); };\n"
        f"await import({json.dumps(Path(DENO_SCRIPT_PATH).as_uri())});\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("parselbox.main.DENO_SCRIPT_PATH", str(entry))
    async with Parselbox() as sandbox:
        sandbox.write_file("shared.txt", "initial")
        result = await sandbox.execute_code("bash('cat shared.txt')")
        assert result.output == "initial"

        sandbox.write_file("shared.txt", "from_host")
        result = await sandbox.execute_code("bash('cat shared.txt')")
        assert result.output == "from_host"

        result = await sandbox.execute_code(
            """js("Deno.writeTextFileSync(resolvePath('shared.txt'), 'from_js')");
bash('cat shared.txt')"""
        )
        assert result.output == "from_js"


async def test_bash_rejects_symlink_outside_mount(tmp_path):
    mount = tmp_path / "data"
    private = tmp_path / "private"
    mount.mkdir()
    private.mkdir()
    (private / "secret.txt").write_text("secret", encoding="utf-8")
    _link_directory(mount / "link", private)
    async with Parselbox(
        mounts=[Mount(str(mount), "data")], output_dir=str(tmp_path), timeout=5
    ) as sandbox:
        result = await sandbox.execute_code('bash("cat /mnt/data/link/secret.txt")')
        assert not result.is_success
        assert "secret" not in (result.output or "")
