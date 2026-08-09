from unittest.mock import patch, AsyncMock
from pathlib import Path
from click.testing import CliRunner
from click.exceptions import BadParameter
import pytest

from parselbox.cli import cli, MountType


class TestMountTypeParsing:
    def setup_method(self):
        self.mount_type = MountType()

    def test_full_spec(self):
        mount = self.mount_type.convert("/host:/target:rw", None, None)
        assert mount.host.endswith("host")
        assert mount.target == "/target"
        assert mount.mode == "rw"

    def test_no_mode_defaults_ro(self):
        mount = self.mount_type.convert("/host:/target", None, None)
        assert mount.mode == "ro"

    def test_host_only_defaults_target_and_mode(self):
        mount = self.mount_type.convert("/some/path", None, None)
        assert mount.target == "path"
        assert mount.mode == "ro"

    def test_quoted_host_with_colon(self):
        mount = self.mount_type.convert('"C:\\Users\\data":/target:rw', None, None)
        assert "C:" in mount.host or "Users" in mount.host
        assert mount.target == "/target"
        assert mount.mode == "rw"

    def test_quoted_host_with_spaces(self):
        mount = self.mount_type.convert('"path with spaces":/target', None, None)
        assert "path with spaces" in mount.host
        assert mount.target == "/target"

    def test_empty_target_uses_basename(self):
        mount = self.mount_type.convert("/some/mydir:", None, None)
        assert mount.target == "mydir"

    def test_invalid_mode_raises(self):
        with pytest.raises(BadParameter, match="must be 'ro' or 'rw'"):
            self.mount_type.convert("/host:/target:badmode", None, None)

    def test_empty_spec_raises(self):
        with pytest.raises(BadParameter):
            self.mount_type.convert("", None, None)


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_sandbox():
    with patch("parselbox.cli.Parselbox") as MockClass:
        mock_instance = MockClass.return_value
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=None)
        mock_instance.run_mcp = AsyncMock(return_value=None)
        yield MockClass


def test_defaults(runner, mock_sandbox):
    result = runner.invoke(cli, [], catch_exceptions=False)
    assert result.exit_code == 0

    assert mock_sandbox.called
    _, kwargs = mock_sandbox.call_args
    assert kwargs.get("network") is False

    instance = mock_sandbox.return_value
    instance.run_mcp.assert_awaited_with(
        transport="stdio", host="0.0.0.0", port=9000, elicit=False
    )


def test_transport_http(runner, mock_sandbox):
    result = runner.invoke(
        cli, ["--transport", "http", "--port", "8080"], catch_exceptions=False
    )
    assert result.exit_code == 0

    instance = mock_sandbox.return_value
    instance.run_mcp.assert_awaited_with(
        transport="http", host="0.0.0.0", port=8080, elicit=False
    )


def test_multiple_files(runner, mock_sandbox):
    with runner.isolated_filesystem():
        with open("a.py", "w") as f:
            f.write("print('a')")
        with open("b.py", "w") as f:
            f.write("print('b')")

        result = runner.invoke(
            cli, ["--file", "a.py", "--file", "b.py"], catch_exceptions=False
        )

        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args

        files = kwargs["files"]
        assert len(files) == 2
        assert any("a.py" in f for f in files)


def test_mount_with_target_and_mode(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("data").mkdir()
        result = runner.invoke(
            cli, ["--mount", "data:/target:rw"], catch_exceptions=False
        )
        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        mounts = kwargs["mounts"]
        assert len(mounts) == 1
        assert mounts[0].target == "/target"
        assert mounts[0].mode == "rw"


def test_mount_default_mode_ro(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("data").mkdir()
        result = runner.invoke(cli, ["--mount", "data:/target"], catch_exceptions=False)
        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        mounts = kwargs["mounts"]
        assert mounts[0].mode == "ro"


def test_mount_host_only(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("mydata").mkdir()
        result = runner.invoke(cli, ["--mount", "mydata"], catch_exceptions=False)
        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        mounts = kwargs["mounts"]
        assert mounts[0].target == "mydata"
        assert mounts[0].mode == "ro"


def test_mount_quoted_path(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("path with spaces").mkdir()
        result = runner.invoke(
            cli, ["--mount", '"path with spaces":/target:rw'], catch_exceptions=False
        )
        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        mounts = kwargs["mounts"]
        assert "path with spaces" in mounts[0].host
        assert mounts[0].target == "/target"
        assert mounts[0].mode == "rw"


def test_mount_invalid_mode(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("data").mkdir()
        result = runner.invoke(cli, ["--mount", "data:/target:invalid"])
        assert result.exit_code != 0
        assert "must be 'ro' or 'rw'" in result.output


def test_mount_multiple(runner, mock_sandbox):
    with runner.isolated_filesystem():
        Path("input").mkdir()
        Path("output").mkdir()
        result = runner.invoke(
            cli,
            ["--mount", "input:/in:ro", "--mount", "output:/out:rw"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        mounts = kwargs["mounts"]
        assert len(mounts) == 2
        assert mounts[0].mode == "ro"
        assert mounts[1].mode == "rw"


def test_net_flag_true(runner, mock_sandbox):
    result = runner.invoke(cli, ["--network"], catch_exceptions=False)
    assert result.exit_code == 0
    _, kwargs = mock_sandbox.call_args
    assert kwargs["network"] is True


def test_packages_parsing(runner, mock_sandbox):
    result = runner.invoke(cli, ["--packages", "numpy,pandas"], catch_exceptions=False)
    assert result.exit_code == 0
    _, kwargs = mock_sandbox.call_args
    assert kwargs["packages"] == ["numpy", "pandas"]


def test_autoload_packages(runner, mock_sandbox):
    result = runner.invoke(cli, ["--allow-runtime-packages"], catch_exceptions=False)
    assert result.exit_code == 0
    _, kwargs = mock_sandbox.call_args
    assert kwargs["allow_runtime_packages"] is True


def test_mcp_config_file(runner, mock_sandbox):
    with runner.isolated_filesystem():
        with open("mcp.json", "w") as f:
            f.write("{}")
        result = runner.invoke(cli, ["--mcp", "mcp.json"], catch_exceptions=False)

        assert result.exit_code == 0
        _, kwargs = mock_sandbox.call_args
        assert kwargs["mcp"] == "mcp.json"


def test_mcp_config_json_string(runner, mock_sandbox):
    config = '{"mcpServers": {"test": {"url": "http://localhost:8080"}}}'
    result = runner.invoke(cli, ["--mcp", config], catch_exceptions=False)

    assert result.exit_code == 0
    _, kwargs = mock_sandbox.call_args
    assert kwargs["mcp"] == config


def test_env_parsing(runner, mock_sandbox):
    result = runner.invoke(
        cli,
        ["--env", "FOO=bar", "--env", "BAZ=qux=extra"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    _, kwargs = mock_sandbox.call_args
    assert kwargs["env"] == {"FOO": "bar", "BAZ": "qux=extra"}


def test_env_invalid_raises(runner, mock_sandbox):
    result = runner.invoke(cli, ["--env", "NOEQUALS"])
    assert result.exit_code != 0
    assert "KEY=VALUE" in result.output
