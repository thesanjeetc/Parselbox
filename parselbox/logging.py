import logging
import re
import sys

TAG_PATTERN = re.compile(r"\[SANDBOX:\((.*?)\)\]\s*")

RESET = "\033[0m"
WHITE = "\033[97m"
DARK_GRAY = "\033[38;5;244m"

LEVEL_COLORS = {
    "TRACE": "\033[38;5;245m",
    "DEBUG": "\033[38;5;75m",
    "INFO": "\033[38;5;79m",
    "SUCCESS": "\033[38;5;79m",
    "WARNING": "\033[38;5;222m",
    "ERROR": "\033[38;5;197m",
    "CRITICAL": "\033[38;5;197m",
}


def _normalize_record(record: logging.LogRecord) -> tuple[str, str]:
    msg = record.getMessage()
    name = record.name

    if hasattr(record, "component"):
        return record.component.upper(), msg

    match = TAG_PATTERN.search(msg)
    if match:
        tag_raw = match.group(1).upper()
        clean_msg = TAG_PATTERN.sub("", msg).strip()

        if "PYODIDE" in tag_raw:
            return "PYODIDE", clean_msg
        elif "DENO" in tag_raw:
            return "DENO", clean_msg
        return "SANDBOX", clean_msg

    if "uvicorn" in name:
        return "SERVER", msg

    if "fastmcp" in name:
        return "SERVER", msg

    return "CLIENT", msg


NOISY_LOGGERS = ("httpx", "httpcore", "mcp", "fastmcp", "docket")


class ColoredHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            if record.name.startswith(NOISY_LOGGERS) and record.levelno <= logging.INFO:
                if self.level > logging.DEBUG:
                    return

            comp, clean_msg = _normalize_record(record)
            level = record.levelname
            time_str = (
                self.formatter.formatTime(record, "%H:%M:%S") if self.formatter else ""
            )

            if sys.stderr.isatty():
                level_color = LEVEL_COLORS.get(level, LEVEL_COLORS["INFO"])
                output = (
                    f"{DARK_GRAY}{time_str}{RESET}  "
                    f"{DARK_GRAY}{comp: <8}{RESET} "
                    f"{level_color}{level: <7}{RESET}  "
                    f"{WHITE}{clean_msg}{RESET}\n"
                )
            else:
                output = f"{time_str}  {comp: <8} {level: <7}  {clean_msg}\n"
            sys.stderr.write(output)
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


def get_logger(name: str = "parselbox") -> logging.Logger:
    return logging.getLogger(name)


logger = get_logger()


def configure_logger(log_level: str = "INFO"):
    level = getattr(logging, log_level.upper(), logging.INFO)

    handler = ColoredHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter())

    logging.captureWarnings(True)

    for log_name in [
        "parselbox",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastmcp",
        "fastmcp.server",
        "fastmcp.client",
        "mcp",
        "mcp.server",
        "mcp.client",
        "httpx",
        "py.warnings",
    ]:
        mod_logger = logging.getLogger(log_name)
        mod_logger.handlers = [handler]
        mod_logger.setLevel(level)
        mod_logger.propagate = False
