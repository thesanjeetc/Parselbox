from textwrap import dedent

import pytest

from parselbox import Parselbox, Mount
from parselbox.models import SandboxError


class TestFileSystem:
    async def test_multiple_input_files(self, tmp_path):
        file_a = tmp_path / "a.txt"
        file_b = tmp_path / "b.txt"
        file_a.write_text("AAA")
        file_b.write_text("BBB")

        async with Parselbox(files=[str(file_a), str(file_b)]) as sandbox:
            code = dedent(
                """
                with open('/files/a.txt') as fa, open('/files/b.txt') as fb:
                    res = fa.read() + fb.read()
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output == "AAABBB"

    async def test_binary_file_io(self, tmp_path):
        bin_file = tmp_path / "image.bin"
        bin_file.write_bytes(b"\x00\xff\x10\x20")

        async with Parselbox(files=[str(bin_file)]) as sandbox:
            code = dedent(
                """
                with open('/files/image.bin', 'rb') as f:
                    data = f.read()
                data == b'\\x00\\xFF\\x10\\x20'
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output is True

    async def test_mount_explicit_read_write(self, tmp_path):
        host_work = tmp_path / "workspace"
        host_work.mkdir()

        mount = Mount(host=str(host_work), target="work", mode="rw")

        async with Parselbox(mounts=[mount]) as sandbox:
            code = dedent(
                """
                with open('/mnt/work/processed.txt', 'w') as f:
                    f.write('COMPLETED')
            """
            )
            await sandbox.execute_code(code)

        assert (host_work / "processed.txt").exists()
        assert (host_work / "processed.txt").read_text() == "COMPLETED"

    async def test_mixed_input_styles(self, tmp_path):
        ro_dir = tmp_path / "refs"
        rw_dir = tmp_path / "logs"
        ro_dir.mkdir()
        rw_dir.mkdir()
        (ro_dir / "ref.txt").write_text("Ref Data")

        mounts = [
            Mount(host=str(ro_dir), target="refs", mode="ro"),
            Mount(host=str(rw_dir), target="logs", mode="rw"),
        ]

        async with Parselbox(mounts=mounts) as sandbox:
            result = await sandbox.execute_code("open('/mnt/refs/ref.txt').read()")
            assert result.output == "Ref Data"

            result = await sandbox.execute_code(
                "open('/mnt/refs/ref.txt', 'w').write('fail')"
            )
            assert result.is_success is False
            assert "permissionerror" in result.error.lower()

            await sandbox.execute_code(
                "open('/mnt/logs/app.log', 'w').write('LogEntry')"
            )

        assert (rw_dir / "app.log").read_text() == "LogEntry"


class TestOutputDir:
    async def test_output_dir_syncs_to_host(self, tmp_path):
        output = tmp_path / "output"
        output.mkdir()

        async with Parselbox(output_dir=str(output)) as sandbox:
            await sandbox.execute_code("open('report.txt', 'w').write('done')")

        assert (output / "report.txt").read_text() == "done"

    async def test_result_files_lists_created_files(self, tmp_path):
        output = tmp_path / "output"
        output.mkdir()

        async with Parselbox(output_dir=str(output)) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                    open('a.txt', 'w').write('A')
                    open('b.txt', 'w').write('B')
                    'ok'
                """)
            )
            assert result.is_success
            assert "/workspace/a.txt" in result.files
            assert "/workspace/b.txt" in result.files

    async def test_result_files_empty_when_no_files_created(self):
        async with Parselbox() as sandbox:
            result = await sandbox.execute_code("1 + 1")
            assert result.files == []


class TestResolvePath:
    def test_workspace_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert sbx.resolve_path("/workspace/file.txt") == tmp_path / "file.txt"

    def test_nested_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert (
            sbx.resolve_path("/workspace/a/b/c.txt") == tmp_path / "a" / "b" / "c.txt"
        )

    def test_files_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        p = sbx.resolve_path("/files/data.csv")
        assert p.name == "data.csv"

    def test_tmp_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        p = sbx.resolve_path("/tmp/scratch.txt")
        assert p.name == "scratch.txt"

    def test_mount_path(self, tmp_path):
        mount_dir = tmp_path / "data"
        mount_dir.mkdir()
        sbx = Parselbox(
            output_dir=str(tmp_path),
            mounts=[Mount(host=str(mount_dir), target="data", mode="ro")],
        )
        assert sbx.resolve_path("/mnt/data/file.json") == mount_dir / "file.json"

    def test_relative_resolves_to_workspace(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert sbx.resolve_path("report.txt") == tmp_path / "report.txt"
        assert sbx.resolve_path("sub/file.txt") == tmp_path / "sub" / "file.txt"

    def test_dot_relative_resolves(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert sbx.resolve_path("./report.txt") == tmp_path / "report.txt"
        assert sbx.resolve_path("sub/../file.txt") == tmp_path / "file.txt"

    def test_rejects_invalid_paths(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        for bad in ["/etc/passwd", "/home/user", "/usr/bin/ls"]:
            with pytest.raises(SandboxError, match="not backed by disk"):
                sbx.resolve_path(bad)

    def test_rejects_traversal(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        for bad in [
            "../../etc/passwd",
            "../../../etc/shadow",
            "/workspace/../../etc/passwd",
        ]:
            with pytest.raises(SandboxError, match="not backed by disk"):
                sbx.resolve_path(bad)

    def test_rejects_prefix_mismatch(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        with pytest.raises(SandboxError):
            sbx.resolve_path("/workspacex/file.txt")

    def test_trailing_slash(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert sbx.resolve_path("/workspace/dir/") == tmp_path / "dir"

    def test_bare_workspace(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        assert sbx.resolve_path("/workspace") == tmp_path

    def test_nested_mounts_longer_prefix_wins(self, tmp_path):
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        outer.mkdir()
        inner.mkdir()
        sbx = Parselbox(
            output_dir=str(tmp_path),
            mounts=[
                Mount(host=str(outer), target="data", mode="ro"),
                Mount(host=str(inner), target="data/sub", mode="ro"),
            ],
        )
        assert sbx.resolve_path("/mnt/data/sub/f.txt") == inner / "f.txt"
        assert sbx.resolve_path("/mnt/data/f.txt") == outer / "f.txt"


class TestReadFile:
    async def test_read_text_file(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("open('hello.txt', 'w').write('hello world')")
            data = sandbox.read_file("/workspace/hello.txt")
            assert data == "hello world"

    async def test_read_binary_file(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code(
                "open('test.png', 'wb').write(b'\\x89PNG\\r\\n\\x1a\\n\\x00\\x00')"
            )
            data = sandbox.read_file("/workspace/test.png")
            assert isinstance(data, bytes)
            assert data[:4] == b"\x89PNG"

    async def test_read_nonexistent_file(self):
        async with Parselbox() as sandbox:
            with pytest.raises(SandboxError, match="File not found"):
                sandbox.read_file("/workspace/nope.txt")

    async def test_read_directory_raises(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        (tmp_path / "subdir").mkdir()
        with pytest.raises(SandboxError, match="directory"):
            sbx.read_file("/workspace/subdir")

    async def test_read_invalid_path_raises(self):
        async with Parselbox() as sandbox:
            with pytest.raises(SandboxError):
                sandbox.read_file("/etc/passwd")

    def test_read_relative_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        (tmp_path / "rel.txt").write_text("relative")
        assert sbx.read_file("rel.txt") == "relative"

    def test_read_from_files(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        files_dir = sbx.files_dir
        (sbx.resolve_path("/files")).mkdir(parents=True, exist_ok=True)
        sbx.write_file("/files/upload.txt", "uploaded")
        assert sbx.read_file("/files/upload.txt") == "uploaded"

    def test_read_from_mount(self, tmp_path):
        mount_dir = tmp_path / "ext"
        mount_dir.mkdir()
        (mount_dir / "ref.txt").write_text("reference")
        sbx = Parselbox(
            output_dir=str(tmp_path),
            mounts=[Mount(host=str(mount_dir), target="ext", mode="ro")],
        )
        assert sbx.read_file("/mnt/ext/ref.txt") == "reference"

    def test_text_vs_binary_detection(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))

        (tmp_path / "f.txt").write_text("hello")
        assert isinstance(sbx.read_file("/workspace/f.txt"), str)

        (tmp_path / "f.json").write_text('{"a":1}')
        assert isinstance(sbx.read_file("/workspace/f.json"), str)

        (tmp_path / "f.csv").write_text("a,b\n1,2")
        assert isinstance(sbx.read_file("/workspace/f.csv"), str)

        (tmp_path / "f.png").write_bytes(b"\x89PNG\x00\x00")
        assert isinstance(sbx.read_file("/workspace/f.png"), bytes)

        (tmp_path / "f.pdf").write_bytes(b"%PDF\x00data")
        assert isinstance(sbx.read_file("/workspace/f.pdf"), bytes)

        (tmp_path / "f.zip").write_bytes(b"PK\x03\x04\x00")
        assert isinstance(sbx.read_file("/workspace/f.zip"), bytes)

    def test_no_extension_detection(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        (tmp_path / "Makefile").write_text("all: build")
        assert isinstance(sbx.read_file("/workspace/Makefile"), str)

        (tmp_path / "blob").write_bytes(b"\x00\x01\x02")
        assert isinstance(sbx.read_file("/workspace/blob"), bytes)

    def test_empty_file(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        (tmp_path / "empty").write_bytes(b"")
        assert sbx.read_file("/workspace/empty") == ""


class TestWriteFile:
    def test_write_text(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/hello.txt", "hello world")
        assert (tmp_path / "hello.txt").read_text() == "hello world"

    def test_write_binary(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/img.png", b"\x89PNG\r\n")
        assert (tmp_path / "img.png").read_bytes() == b"\x89PNG\r\n"

    def test_write_creates_parent_dirs(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/a/b/c/deep.txt", "deep")
        assert (tmp_path / "a" / "b" / "c" / "deep.txt").read_text() == "deep"

    def test_write_overwrite(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/f.txt", "first")
        sbx.write_file("/workspace/f.txt", "second")
        assert (tmp_path / "f.txt").read_text() == "second"

    def test_write_to_files(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/files/input.csv", "a,b")
        assert sbx.read_file("/files/input.csv") == "a,b"

    def test_write_to_tmp(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/tmp/scratch.txt", "temp")
        assert sbx.read_file("/tmp/scratch.txt") == "temp"

    def test_write_relative(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("rel.txt", "relative")
        assert (tmp_path / "rel.txt").read_text() == "relative"

    def test_write_rejects_invalid_path(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        with pytest.raises(SandboxError):
            sbx.write_file("/etc/evil.txt", "nope")

    def test_write_rejects_traversal(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        with pytest.raises(SandboxError):
            sbx.write_file("../../etc/passwd", "nope")

    def test_write_empty(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/e.txt", "")
        assert sbx.read_file("/workspace/e.txt") == ""


class TestRoundtrip:
    def test_text_roundtrip(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        sbx.write_file("/workspace/rt.txt", "roundtrip")
        assert sbx.read_file("/workspace/rt.txt") == "roundtrip"

    def test_binary_roundtrip(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        blob = bytes(range(256))
        sbx.write_file("/workspace/rt.bin", blob)
        assert sbx.read_file("/workspace/rt.bin") == blob

    def test_unicode_roundtrip(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        text = "Unicode: \u00e9\u00e8\u00ea \u4e16\u754c \U0001f680"
        sbx.write_file("/workspace/uni.txt", text)
        assert sbx.read_file("/workspace/uni.txt") == text

    def test_large_text_roundtrip(self, tmp_path):
        sbx = Parselbox(output_dir=str(tmp_path))
        big = "x" * (1024 * 1024)
        sbx.write_file("/workspace/big.txt", big)
        assert len(sbx.read_file("/workspace/big.txt")) == 1024 * 1024

    def test_large_binary_roundtrip(self, tmp_path):
        import os

        sbx = Parselbox(output_dir=str(tmp_path))
        big = os.urandom(1024 * 1024)
        sbx.write_file("/workspace/big.bin", big)
        assert sbx.read_file("/workspace/big.bin") == big

    async def test_execute_then_read(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("open('out.txt', 'w').write('from sandbox')")
            assert sandbox.read_file("/workspace/out.txt") == "from sandbox"

    async def test_write_then_execute_read(self):
        async with Parselbox() as sandbox:
            sandbox.write_file("/workspace/input.txt", "injected")
            result = await sandbox.execute_code("open('input.txt').read()")
            assert result.output == "injected"


class TestThreeWayFilesystem:
    async def test_python_write_bash_read_js_read(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("open('shared.txt', 'w').write('from_python')")
            r = await sandbox.execute_code('bash("cat shared.txt")')
            assert "from_python" in r.output
            r = await sandbox.execute_code(
                "js(\"return Deno.readTextFileSync(resolvePath('shared.txt'))\")"
            )
            assert r.output == "from_python"

    async def test_bash_write_python_read_js_read(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code('bash("echo from_bash > b.txt")')
            r = await sandbox.execute_code("open('b.txt').read().strip()")
            assert r.output == "from_bash"
            r = await sandbox.execute_code(
                "js(\"return Deno.readTextFileSync(resolvePath('b.txt')).trim()\")"
            )
            assert r.output == "from_bash"

    async def test_js_write_python_read_bash_read(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code(
                "js(\"Deno.writeTextFileSync(resolvePath('j.txt'), 'from_js')\")"
            )
            r = await sandbox.execute_code("open('j.txt').read()")
            assert r.output == "from_js"
            r = await sandbox.execute_code('bash("cat j.txt")')
            assert "from_js" in r.output

    async def test_overwrite_cycle(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("open('cycle.txt', 'w').write('v1')")
            r = await sandbox.execute_code('bash("cat cycle.txt")')
            assert "v1" in r.output

            await sandbox.execute_code('bash("echo v2 > cycle.txt")')
            r = await sandbox.execute_code("open('cycle.txt').read().strip()")
            assert r.output == "v2"

            await sandbox.execute_code(
                "js(\"Deno.writeTextFileSync(resolvePath('cycle.txt'), 'v3')\")"
            )
            r = await sandbox.execute_code("open('cycle.txt').read()")
            assert r.output == "v3"
            r = await sandbox.execute_code('bash("cat cycle.txt")')
            assert "v3" in r.output

    async def test_delete_visible_across_runtimes(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("open('del.txt', 'w').write('exists')")
            await sandbox.execute_code('bash("rm del.txt")')
            r = await sandbox.execute_code("import os; os.path.exists('del.txt')")
            assert r.output is False

    async def test_mkdir_visible_across_runtimes(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code('bash("mkdir -p subdir/nested")')
            r = await sandbox.execute_code("import os; os.path.isdir('subdir/nested')")
            assert r.output is True
            await sandbox.execute_code("open('subdir/nested/f.txt', 'w').write('deep')")
            r = await sandbox.execute_code('bash("cat subdir/nested/f.txt")')
            assert "deep" in r.output
