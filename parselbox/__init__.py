from importlib.metadata import PackageNotFoundError, version as _version

try:
    __version__ = _version("parselbox")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+unknown"

from .logging import configure_logger

# Logging enabled by default. Disable with:
#   import logging; logging.getLogger("parselbox").setLevel(logging.WARNING)
configure_logger()

from .hooks import Hook
from .main import Parselbox
from .models import Callback, ExecutionResult, Mount, SandboxError
from .mcp import ParselboxMCP
from .prompt import PARSELBOX_PROMPT

__all__ = [
    "__version__",
    "Callback",
    "ExecutionResult",
    "Hook",
    "Mount",
    "ParselboxMCP",
    "Parselbox",
    "SandboxError",
    "PARSELBOX_PROMPT",
    "configure_logger",
]
