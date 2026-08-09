import asyncio
import csv
import sys
import traceback
from io import StringIO
from typing import Any

import click

from .logging import configure_logger
from .main import Parselbox
from .models import Mount


class MountType(click.ParamType):
    """
    Parse mount specs: HOST:TARGET:MODE

    Examples:
        /path:/target:ro
        /path:/target           (mode defaults to ro)
        /path                   (target defaults to basename)
        "C:\\data":/target:rw   (quotes for Windows paths)
    """

    name = "mount"

    def convert(
        self, value: str, param: click.Parameter | None, ctx: click.Context | None
    ) -> Mount:
        try:
            parts = next(csv.reader(StringIO(value), delimiter=":"))
        except Exception as e:
            self.fail(f"Invalid mount spec '{value}': {e}", param, ctx)

        if not parts or not parts[0]:
            self.fail(f"Missing host path in mount spec: {value}", param, ctx)

        host = parts[0]
        target = parts[1] if len(parts) > 1 and parts[1] else None
        mode = parts[2] if len(parts) > 2 and parts[2] else "ro"

        if mode not in ("ro", "rw"):
            self.fail(f"Invalid mode '{mode}', must be 'ro' or 'rw'", param, ctx)

        return Mount(host=host, target=target, mode=mode)


async def _run_server(
    opts: dict[str, Any], transport: str, host: str, port: int, elicit: bool
) -> None:
    sandbox = Parselbox(**opts)
    async with sandbox:
        await sandbox.run_mcp(transport=transport, host=host, port=port, elicit=elicit)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--mcp",
    type=str,
    help="MCP config: file path or JSON string.",
)
@click.option(
    "--transport",
    type=click.Choice(["stdio", "http"], case_sensitive=False),
    default="stdio",
    show_default=True,
    help="MCP transport type.",
)
@click.option(
    "--host",
    type=str,
    default="0.0.0.0",
    help="Host (for HTTP transport).",
)
@click.option(
    "--port",
    type=int,
    default=9000,
    help="Port number (for HTTP transport).",
)
@click.option(
    "--file",
    "files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Files to load into /files/ (read/write).",
)
@click.option(
    "--mount",
    "mounts",
    multiple=True,
    type=MountType(),
    help="Mount directories (HOST:TARGET:MODE). MODE is 'ro' or 'rw' (default: ro). Quote Windows paths.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, resolve_path=True),
    help="Directory to persist outputs (read/write).",
)
@click.option(
    "--packages",
    type=str,
    help="Comma-separated packages to install.",
)
@click.option(
    "--package-dir",
    type=click.Path(file_okay=False, resolve_path=True),
    help="Directory to persist packages across sessions.",
)
@click.option(
    "--allow-runtime-packages",
    is_flag=True,
    default=False,
    help="Auto-install packages from imports.",
)
@click.option(
    "--network",
    is_flag=True,
    default=False,
    help="Enable network access (default: False).",
)
@click.option(
    "--memory",
    type=int,
    default=2048,
    show_default=True,
    help="WASM memory limit in MB.",
)
@click.option(
    "--timeout",
    type=int,
    default=60,
    show_default=True,
    help="Execution timeout in seconds (0 = no timeout).",
)
@click.option(
    "--serve",
    type=int,
    default=None,
    help="Start HTTP server on this port.",
)
@click.option(
    "--env",
    multiple=True,
    type=str,
    help="Environment variable (KEY=VALUE). Can be repeated.",
)
@click.option(
    "--elicit",
    is_flag=True,
    default=False,
    help="Enable MCP elicitation.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
def cli(
    mcp: str | None,
    transport: str,
    host: str,
    port: int,
    files: tuple[str, ...],
    mounts: tuple[Mount, ...],
    output_dir: str | None,
    packages: str | None,
    package_dir: str | None,
    allow_runtime_packages: bool,
    network: bool,
    memory: int,
    timeout: int,
    serve: int | None,
    env: tuple[str, ...],
    elicit: bool,
    verbose: bool,
):
    configure_logger("DEBUG" if verbose else "INFO")

    opts: dict[str, Any] = {}

    if mcp:
        opts["mcp"] = mcp
    if output_dir:
        opts["output_dir"] = output_dir
    if files:
        opts["files"] = list(files)
    if packages:
        opts["packages"] = [p.strip() for p in packages.split(",") if p.strip()]
    if package_dir:
        opts["package_dir"] = package_dir
    if mounts:
        opts["mounts"] = list(mounts)
    if serve:
        opts["serve"] = serve

    opts["allow_runtime_packages"] = allow_runtime_packages
    opts["memory"] = memory
    opts["timeout"] = timeout
    opts["network"] = network
    if env:
        env_dict = {}
        for v in env:
            if "=" not in v:
                raise click.BadParameter(
                    f"Expected KEY=VALUE, got '{v}'", param_hint="--env"
                )
            key, val = v.split("=", 1)
            env_dict[key] = val
        opts["env"] = env_dict

    try:
        asyncio.run(_run_server(opts, transport, host, port, elicit))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        click.secho("An unexpected error occurred:", fg="red")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    cli()
