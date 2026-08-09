import re
from pathlib import Path
from typing import Any

from parselbox.bridge import Bridge


class Toolkit(Bridge):
    def __init__(self):
        self._search_index = None

    def help(self) -> str:
        """Return a comprehensive guide on how to use the Parselbox sandbox."""
        return self._pbx_sandbox.get_prompt()

    def _build_index(self):
        self._search_index = []
        for ns, ctx in self._pbx_sandbox.context.items():
            if ns == "sbx" or not isinstance(ctx, Bridge):
                continue
            label = f"{ns} ({ctx._pbx_type})"
            prepend = type(ctx)._pbx_searchable is Bridge._pbx_searchable
            for path, meta in ctx._pbx_searchable().items():
                full = f"{ns}.{path}" if prepend and path else (ns if prepend else path)
                self._search_index.append((full, meta, label))

    def search(self, pattern: str) -> dict:
        """
        Search for tools across all namespaces by name, description, or parameter.

        Args:
            pattern: Regex pattern to match against tool names, descriptions, and parameters.

        Returns:
            dict of {"namespace (type)": [matches]} with matching tools grouped by namespace.
        """
        if not pattern or not pattern.strip():
            return {}
        self._build_index()
        index = self._search_index

        try:
            regex = re.compile(pattern, re.IGNORECASE)
            match = lambda t: bool(regex.search(t))
        except re.error:
            p = pattern.lower()
            match = lambda t: p in t.lower()

        buckets = {}
        for full, meta, label in index:
            text = f"{full} {meta.desc} {' '.join(meta.params)} {' '.join(meta.fields)}"
            if match(text):
                buckets.setdefault(label, []).append(
                    {"path": full, "description": meta.desc}
                )
        return buckets

    def info(self) -> dict:
        """
        Get sandbox environment information.

        Returns {context, environment, mounts, files, skills, serve} describing available
        external tools, network/package settings, filesystem state and skills on sandbox start.
        """
        context = {"globals": [], "namespaces": []}

        if self._pbx_sandbox.context:
            for name, val in self._pbx_sandbox.context.items():
                if callable(val):
                    context["globals"].append(f"{name} (function)")
                elif val._pbx_tool_index:
                    suffix = "" if val._pbx_type == "function" else ".*"
                    context["namespaces"].append(f"{name}{suffix} ({val._pbx_type})")

        if self._pbx_sandbox.globals:
            for name, val in self._pbx_sandbox.globals.items():
                context["globals"].append(f"{name} ({type(val).__name__})")

        environment = {
            "network": self._pbx_sandbox.network,
            "allow_runtime_packages": self._pbx_sandbox.allow_runtime_packages,
            "packages": self._pbx_sandbox.packages,
        }

        mounts = (
            [
                f"/mnt/{m.target.strip('/')} ({'read-only' if m.mode == 'ro' else 'read/write'})"
                for m in self._pbx_sandbox.mounts
            ]
            if self._pbx_sandbox.mounts
            else None
        )

        files = None
        max_files = 5
        if self._pbx_sandbox.files:
            file_names = [Path(f).name for f in self._pbx_sandbox.files]
            if len(file_names) > max_files:
                files = {
                    "path": "/files (read/write)",
                    "items": file_names[:max_files]
                    + [f"...({len(file_names) - max_files} more)"],
                }
            else:
                files = {"path": "/files (read/write)", "items": file_names}

        skills_info = None
        if self._pbx_sandbox.skills_dir:
            skills_info = "/mnt/skills"

        serve = (
            f"http://localhost:{self._pbx_sandbox.serve}"
            if self._pbx_sandbox.serve
            else None
        )

        return {
            "context": context,
            "environment": environment,
            "mounts": mounts,
            "files": files,
            "skills": skills_info,
            "serve": serve,
        }

    def inspect(self, tool_names: str | list[str]) -> dict:
        """
        Get detailed signatures and documentation for tools.

        Use this before calling a tool to understand its parameters and return type if available.

        Args:
            tool_names: Tool reference or list of references (e.g. "namespace.tool" or ["ns.tool1", "ns.tool2"])

        Returns:
            dict: tool_name -> {description, input schema, output schema}
        """
        if isinstance(tool_names, str):
            tool_names = [tool_names]
        results = {}
        for name in tool_names:
            parts = name.split(".")
            root = self._pbx_sandbox.context.get(parts[0])
            if not root:
                continue
            path = ".".join(parts[1:])

            info = root._pbx_tool_info(path if path else "")
            if info:
                results[name] = info
                continue

            target = root
            for part in parts[1:]:
                target = getattr(target, part, None)
                if target is None:
                    break
            if target is not None and isinstance(target, Bridge):
                attrs = dir(target)
                results[name] = {
                    "methods": [a for a in attrs if callable(getattr(target, a, None))],
                    "properties": [
                        a for a in attrs if isinstance(getattr(target, a, None), Bridge)
                    ],
                }
            else:
                results[name] = {"error": f"'{name}' not found"}
        return results

    def preview(self, data: Any) -> Any:
        """
        Generate a condensed summary of large or deeply nested data.

        Use this to inspect the schema of responses when structure is unknown.
        Preserves dict keys for schema inspection but truncates content to save tokens.

        Args:
            data: Any data structure to summarize

        Returns:
            Truncated version of the input with same structure
        """
        return self._preview_recursive(data, depth=0)

    def _preview_recursive(self, data, depth) -> Any:
        MAX_ITEMS = 2
        MAX_STR_LEN = 200
        MAX_DEPTH = 4

        if depth > MAX_DEPTH:
            return "...(max depth reached)"

        if isinstance(data, list):
            if not data:
                return []

            preview_list = [
                self._preview_recursive(x, depth + 1) for x in data[:MAX_ITEMS]
            ]

            remaining = len(data) - MAX_ITEMS
            if remaining > 0:
                s = "s" if remaining > 1 else ""
                preview_list.append(f"...({remaining} more item{s})")
            return preview_list

        if isinstance(data, dict):
            preview_dict = {}
            for k, v in data.items():
                preview_dict[k] = self._preview_recursive(v, depth + 1)
            return preview_dict

        if isinstance(data, str) and len(data) > MAX_STR_LEN:
            return data[:MAX_STR_LEN] + "..."

        return data
