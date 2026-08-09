import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


class SandboxError(Exception):
    pass


@dataclass
class SearchItem:
    leaf: str
    desc: str
    params: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)

    @classmethod
    def from_schema(cls, path: str, description: str, schema: dict) -> "SearchItem":
        fields = []
        for defn in schema.get("$defs", {}).values():
            fields.extend(defn.get("properties", {}).keys())
        return cls(
            leaf=path.rsplit(".", 1)[-1],
            desc=(description or "")[:120],
            params=list(schema.get("properties", {}).keys()),
            fields=fields,
        )


def _deserialize(obj):
    if isinstance(obj, dict):
        if "__b64__" in obj and len(obj) == 1:
            return base64.b64decode(obj["__b64__"])
        return {k: _deserialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_deserialize(v) for v in obj]
    return obj


@dataclass
class Callback:
    name: str
    op: str = "call"
    path: list[str] = field(default_factory=list)
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    task_id: str | None = None

    def __post_init__(self):
        self.args = _deserialize(self.args)
        self.kwargs = _deserialize(self.kwargs)

    def _truncate(self, val: Any, max_len: int = 50) -> str:
        if isinstance(val, str):
            return repr(val[:max_len] + "..." if len(val) > max_len else val)
        if isinstance(val, dict):
            keys = ", ".join(str(k) for k in list(val.keys())[:3])
            if len(val) > 3:
                keys += ", ..."
            return "{" + keys + "}"
        if isinstance(val, list) and len(val) > 5:
            items = ", ".join(repr(v) for v in val[:3])
            return f"[{items}, ...({len(val)} items)]"
        return repr(val)

    def __repr__(self) -> str:
        args_str = ", ".join(self._truncate(a) for a in self.args)
        kwargs_str = ", ".join(
            f"{k}={self._truncate(v)}" for k, v in self.kwargs.items()
        )
        params = ", ".join(filter(None, [args_str, kwargs_str]))
        path_str = ".".join(self.path) if self.path else ""
        full_name = f"{self.name}.{path_str}" if path_str else self.name
        if self.op != "call":
            return f"{full_name}.{self.op}({params})"
        return f"{full_name}({params})"


@dataclass
class ExecutionResult:
    is_success: bool
    output: Any | None = None
    files: list[str] = field(default_factory=list)
    error: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    # Rendered HTML from display(), if the agent emitted a view this execution.
    view: str | None = None


@dataclass
class Mount:
    host: str | Path
    target: str | None = None
    mode: Literal["ro", "rw"] = "ro"

    def __post_init__(self):
        self.host = str(Path(self.host).resolve())
        if not self.target:
            self.target = Path(self.host).name
