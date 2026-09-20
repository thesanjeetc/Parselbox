import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from parselbox import Parselbox
from parselbox.rpc import RpcClient


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_deno_environment_preserves_only_required_host_variables(monkeypatch, system):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("PARSELBOX_HOST_SECRET", "do-not-inherit")
    monkeypatch.setattr("parselbox.main.platform.system", lambda: system)
    sandbox = Parselbox(env={"EXPLICIT_SETTING": "allowed", "DENO_DIR": "ignored"})
    try:
        env = sandbox._build_deno_env()
        assert env.get("SystemRoot") == (r"C:\Windows" if system == "Windows" else None)
        assert "PARSELBOX_HOST_SECRET" not in env
        assert env["EXPLICIT_SETTING"] == "allowed"
        assert env["DENO_DIR"] != "ignored"
    finally:
        sandbox.cache_dir.cleanup()


def test_npm_preload_uses_sandbox_environment_and_reports_errors(monkeypatch):
    sandbox = Parselbox(env={"DENO_CERT": "explicit-certificate.pem"})
    captured = {}

    def fail_cache(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 1, b"", b"registry lookup failed")

    monkeypatch.setattr("parselbox.main.subprocess.run", fail_cache)
    try:
        with pytest.raises(RuntimeError, match="registry lookup failed"):
            sandbox._setup_packages(["npm:missing-package"])
        assert captured["env"] == sandbox._build_deno_env()
    finally:
        sandbox.cache_dir.cleanup()


async def _process(code):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-u",
        "-c",
        code,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def test_startup_reports_early_exit_and_stderr():
    proc = await _process(
        "import sys; sys.stderr.write('missing dependency'); sys.exit(7)"
    )
    rpc = RpcClient(proc)
    try:
        with pytest.raises(RuntimeError, match=r"exit 7.*missing dependency"):
            await asyncio.wait_for(rpc.start(), timeout=5)
    finally:
        await rpc.close()
        await rpc.close()
    assert proc.returncode == 7


async def test_startup_drains_large_stderr_without_blocking():
    proc = await _process(
        "import sys; sys.stderr.write('x' * 524288); sys.stderr.flush(); "
        'print(\'{"method":"ready"}\', flush=True); sys.stdin.read()'
    )
    rpc = RpcClient(proc)
    try:
        await rpc.start(timeout=5)
        assert 0 < len(rpc._stderr) <= 16384
    finally:
        await rpc.close()
    assert proc.returncode is not None


async def test_startup_timeout_includes_stderr():
    proc = await _process(
        "import sys, time; print('still loading', file=sys.stderr, flush=True); time.sleep(60)"
    )
    rpc = RpcClient(proc)
    try:
        with pytest.raises(RuntimeError, match="within 1s.*still loading"):
            await rpc.start(timeout=1)
    finally:
        await rpc.close()
    assert proc.returncode is not None


async def test_failed_sandbox_startup_cleans_up(monkeypatch, tmp_path):
    script = tmp_path / "fail.ts"
    script.write_text("throw new Error('startup regression test');", encoding="utf-8")
    monkeypatch.setattr("parselbox.main.DENO_SCRIPT_PATH", str(script))
    sandbox = Parselbox()
    with pytest.raises(RuntimeError, match="startup regression test"):
        await asyncio.wait_for(sandbox.connect(), timeout=10)
    assert sandbox._proc is None
    assert sandbox._rpc is None
    assert not Path(sandbox.cache_dir.name).exists()
