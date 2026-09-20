import subprocess
import sys
from textwrap import dedent

import pytest

from parselbox import Parselbox, Mount

pytestmark = pytest.mark.asyncio


class TestPackageCache:
    async def test_package_dir_reused(self, tmp_path):
        cache_dir = tmp_path / "pkg_cache"
        cache_dir.mkdir()

        async with Parselbox(
            packages=["humanize"], network=True, package_dir=str(cache_dir)
        ) as sandbox:
            result = await sandbox.execute_code(
                "import humanize; humanize.intword(1000000)"
            )
            assert result.output == "1.0 million"

        cached_files = {f.name for f in cache_dir.iterdir()}

        async with Parselbox(
            packages=["humanize"], network=True, package_dir=str(cache_dir)
        ) as sandbox:
            result = await sandbox.execute_code(
                "import humanize; humanize.intword(1000000)"
            )
            assert result.output == "1.0 million"

        assert {f.name for f in cache_dir.iterdir()} == cached_files


class TestLocalWheel:
    @staticmethod
    def _build_wheel(tmp_path, name="mypkg", version="1.0.0"):
        """Build a minimal .whl file for testing."""
        import io
        import zipfile

        wheel_name = f"{name}-{version}-py3-none-any.whl"
        dist_info = f"{name}-{version}.dist-info"
        buf = io.BytesIO()

        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(f"{name}/__init__.py", f'VALUE = "{name}-{version}"\n')
            zf.writestr(
                f"{dist_info}/METADATA",
                f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
            )
            zf.writestr(
                f"{dist_info}/WHEEL",
                "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
            zf.writestr(f"{dist_info}/RECORD", "")
            zf.writestr(f"{dist_info}/top_level.txt", f"{name}\n")

        wheel_path = tmp_path / wheel_name
        wheel_path.write_bytes(buf.getvalue())
        return wheel_path

    @pytest.mark.parametrize(
        "directory", ["wheels", "wheels with spaces", "wheels # % 日本語"]
    )
    async def test_local_wheel_file_uri(self, tmp_path, directory):
        wheels_dir = tmp_path / directory
        wheels_dir.mkdir()
        wheel_path = self._build_wheel(wheels_dir)

        async with Parselbox(
            packages=[wheel_path.as_uri()],
            mounts=[Mount(host=str(wheels_dir), target="wheels")],
        ) as sandbox:
            result = await sandbox.execute_code("import mypkg; mypkg.VALUE")
            assert result.output == "mypkg-1.0.0"

    @pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
    async def test_local_wheel_needs_no_staging_and_cleans_up(self, tmp_path, valid):
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        wheel_path = self._build_wheel(wheels_dir)
        if not valid:
            wheel_path.write_bytes(b"invalid wheel archive")
        original = wheel_path.read_bytes()

        async with Parselbox(
            packages=["micropip"],
            allow_runtime_packages=True,
            mounts=[Mount(host=str(wheels_dir), target="wheels")],
        ) as sandbox:
            result = await sandbox.execute_code(
                dedent(f"""
                import os
                from js import Deno
                from pyodide.code import run_js
                before = sorted(os.listdir('/tmp'))
                originals = {{
                    name: getattr(Deno, name)
                    for name in ('readFile', 'readFileSync', 'copyFile', 'copyFileSync')
                }}
                blocked = run_js('() => {{ throw new Error("wheel staging forbidden"); }}')
                try:
                    for name in originals:
                        setattr(Deno, name, blocked)
                    installed = await ParselboxPackages.install([{wheel_path.as_uri()!r}])
                finally:
                    for name, fn in originals.items():
                        setattr(Deno, name, fn)
                {{'installation': installed, 'cleaned': sorted(os.listdir('/tmp')) == before}}
                """)
            )
            assert result.is_success, result.error
            expected = {"installed": [], "failed": []}
            expected["installed" if valid else "failed"] = [wheel_path.as_uri()]
            assert result.output == {"installation": expected, "cleaned": True}
            assert wheel_path.read_bytes() == original
            assert list(wheels_dir.iterdir()) == [wheel_path]

    async def test_local_wheel_outside_allowed_paths_is_denied(self, tmp_path):
        wheel_path = self._build_wheel(tmp_path)
        async with Parselbox(
            packages=["micropip"], allow_runtime_packages=True
        ) as sandbox:
            result = await sandbox.execute_code(
                dedent(f"""
                import os
                before = sorted(os.listdir('/tmp'))
                installed = await ParselboxPackages.install([{wheel_path.as_uri()!r}])
                {{'installation': installed, 'cleaned': sorted(os.listdir('/tmp')) == before}}
                """)
            )
            assert result.is_success, result.error
            assert result.output == {
                "installation": {"installed": [], "failed": [wheel_path.as_uri()]},
                "cleaned": True,
            }

    async def test_failed_unmount_preserves_writable_source_files(self, tmp_path):
        wheels_dir = tmp_path / "wheels"
        wheels_dir.mkdir()
        wheel_path = self._build_wheel(wheels_dir)
        original = wheel_path.read_bytes()
        sentinel = wheels_dir / "keep.txt"
        sentinel.write_bytes(b"must survive cleanup")
        async with Parselbox(
            packages=["micropip"],
            allow_runtime_packages=True,
            mounts=[Mount(str(wheels_dir), "wheels", "rw")],
        ) as sandbox:
            result = await sandbox.execute_code(
                dedent(f"""
                import micropip
                import pyodide_js
                from pyodide.code import run_js
                original_install = micropip.install
                original_unmount = pyodide_js.FS.unmount
                async def fail_install(*args, **kwargs):
                    raise RuntimeError('simulated install failure')
                micropip.install = fail_install
                pyodide_js.FS.unmount = run_js(
                    "() => {{ throw new Error('simulated unmount failure'); }}"
                )
                try:
                    installed = await ParselboxPackages.install([{wheel_path.as_uri()!r}])
                finally:
                    micropip.install = original_install
                    pyodide_js.FS.unmount = original_unmount
                installed
                """)
            )
            assert result.is_success, result.error
            assert result.output == {"installed": [], "failed": [wheel_path.as_uri()]}
        assert wheel_path.read_bytes() == original
        assert sentinel.read_bytes() == b"must survive cleanup"

    async def test_wheel_link_cannot_read_outside_allowed_directory(self, tmp_path):
        wheels_dir = tmp_path / "wheels"
        private = tmp_path / "private"
        wheels_dir.mkdir()
        private.mkdir()
        wheel_path = self._build_wheel(private)
        link = wheels_dir / "link"
        try:
            link.symlink_to(private, target_is_directory=True)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(private)],
                    check=True,
                    capture_output=True,
                )
            else:
                raise
        url = (link / wheel_path.name).as_uri()
        async with Parselbox(
            packages=["micropip"],
            allow_runtime_packages=True,
            mounts=[Mount(str(wheels_dir), "wheels", "ro")],
        ) as sandbox:
            result = await sandbox.execute_code(
                f"await ParselboxPackages.install([{url!r}])"
            )
            assert result.is_success, result.error
            assert result.output == {"installed": [], "failed": [url]}

    async def test_temporary_mount_preserves_read_only_permissions(self, tmp_path):
        wheel_path = self._build_wheel(tmp_path)
        original = wheel_path.read_bytes()
        async with Parselbox(
            packages=["micropip"],
            allow_runtime_packages=True,
            mounts=[Mount(str(tmp_path), "wheels", "ro")],
        ) as sandbox:
            result = await sandbox.execute_code(
                dedent(f"""
                import micropip
                from pathlib import Path
                original_install = micropip.install
                write_denied = False
                async def check_read_only(url):
                    global write_denied
                    try:
                        Path(url.removeprefix('emfs:')).write_bytes(b'overwrite')
                    except PermissionError:
                        write_denied = True
                    return await original_install(url)
                micropip.install = check_read_only
                try:
                    installed = await ParselboxPackages.install([{wheel_path.as_uri()!r}])
                finally:
                    micropip.install = original_install
                {{'installed': installed, 'write_denied': write_denied}}
                """)
            )
            assert result.is_success, result.error
            assert result.output == {
                "installed": {"installed": [wheel_path.as_uri()], "failed": []},
                "write_denied": True,
            }
        assert wheel_path.read_bytes() == original


class TestPackagesAndNetwork:
    async def test_packages_explicit_install(self):
        async with Parselbox(packages=["pytz"], network=True) as sandbox:
            result = await sandbox.execute_code(
                "import pytz; 'UTC' in pytz.all_timezones"
            )
            assert result.output is True

    async def test_allow_runtime_packages_enabled(self):
        async with Parselbox(allow_runtime_packages=True, network=True) as sandbox:
            result = await sandbox.execute_code(
                "import pytz; str(pytz.timezone('US/Pacific'))"
            )
            assert result.output == "US/Pacific"

    async def test_allow_runtime_packages_disabled(self):
        async with Parselbox(allow_runtime_packages=False) as sandbox:
            result = await sandbox.execute_code("import pytz")
            assert result.error is not None
            assert "ModuleNotFoundError" in result.error

    async def test_auto_load_pyodide_mapping(self):
        async with Parselbox(
            allow_runtime_packages=True,
            network=True,
        ) as sandbox:
            result = await sandbox.execute_code(
                """
            import cv2
            import bs4
            import yaml
            import PIL
            import sklearn
            import dateutil
            import numpy as np
            np.array([1,2,3]).sum().item()
            """
            )
            assert result.output == 6

    async def test_auto_load_ignores_local_file(self):
        async with Parselbox(allow_runtime_packages=True, network=True) as sandbox:
            await sandbox.execute_code("open('myutil.py', 'w').write('X = 42')")
            result = await sandbox.execute_code("from myutil import X; X")
            assert result.output == 42

    async def test_auto_load_nonexistent_package(self):
        async with Parselbox(allow_runtime_packages=True, network=True) as sandbox:
            result = await sandbox.execute_code("import fake_pkg_xyz_999")
            assert result.error is not None
            assert "ModuleNotFoundError" in result.error

    @pytest.mark.parametrize(
        "network",
        [False, ["example.com"]],
        ids=["network_disabled", "network_allowlist"],
    )
    async def test_network_blocked(self, network):
        async with Parselbox(packages=["requests"], network=network) as sandbox:
            code = dedent(
                """
                import requests
                try:
                    requests.get('https://google.com')
                    res = "connected"
                except OSError:
                    res = "blocked"
                except Exception as e:
                    res = str(e)
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert "blocked" in str(result.output) or "Permission" in str(result.output)

    async def test_network_denied_no_packages(self):
        async with Parselbox() as sandbox:
            code = dedent(
                """
                from pyodide.http import pyfetch
                try:
                    await pyfetch('https://pypi.org')
                    res = "connected"
                except Exception as e:
                    res = str(e)
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert "connected" not in str(result.output)

    async def test_network_allow_all(self):
        async with Parselbox(packages=["requests"], network=True) as sandbox:
            code = dedent(
                """
                import requests
                try:
                    requests.get('https://www.google.com')
                    res = "connected"
                except Exception as e:
                    res = str(e)
                res
            """
            )
            result = await sandbox.execute_code(code)
            assert result.output == "connected"
