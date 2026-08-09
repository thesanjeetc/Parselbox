from typing import Any

from .logging import logger


class ContextManager:
    def __init__(self, context: dict[str, Any]):
        self.context = context

    async def execute(self, callback):
        if callback.name not in self.context:
            raise AttributeError(f"'{callback.name}' not found")
        path_parts = callback.path or []
        if any(p.startswith("_") for p in path_parts):
            full = f"{callback.name}.{'.'.join(path_parts)}"
            raise AttributeError(f"Tool '{full}' not found")
        root = self.context[callback.name]
        path_str = ".".join(callback.path) if callback.path else ""

        if callback.op == "dir":
            target = root
            for part in callback.path:
                target = getattr(target, part)
            return list(dir(target))

        if callback.op == "help":
            return root._pbx_help(path_str)

        if callback.op != "call":
            raise ValueError(f"Unknown operation: '{callback.op}'")

        if path_str:
            logger.debug(f"Callback: {callback.name}.{path_str}")
        return await root._pbx_dispatch(path_str, callback.kwargs, callback.args)
