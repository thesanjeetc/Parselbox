import fnmatch
import json
import re
from pathlib import Path

import httpx

from parselbox.bridge import Bridge
from parselbox.models import SearchItem


class HTTPBridge(Bridge):
    _pbx_type = "http"
    _SKIP = frozenset(("parameters", "summary", "description", "servers"))
    _METHODS = frozenset(("GET", "POST", "PUT", "PATCH", "DELETE"))

    def __init__(
        self,
        base_url=None,
        token=None,
        headers=None,
        auth=None,
        params=None,
        spec=None,
        timeout=60.0,
    ):
        self._base_url = base_url
        self._headers = headers or {}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._auth = auth
        self._params = params or {}
        self._timeout = timeout
        self._spec_source = spec
        self._spec = None
        self._index = []
        self._by_method = {}
        self._word_index = {}
        self._ref_cache = {}
        self._client = None

    async def _pbx_connect(self):
        if self._spec_source:
            await self._load_spec(self._spec_source)
        base_url = self._base_url
        if not base_url and self._spec:
            servers = self._spec.get("servers", [])
            if servers:
                base_url = servers[0].get("url")
        if not base_url:
            raise ValueError("base_url is required (or provide a spec with servers)")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers=self._headers,
            auth=self._auth,
            params=self._params,
            timeout=httpx.Timeout(self._timeout, connect=min(self._timeout, 5.0)),
            follow_redirects=True,
        )
        self._pbx_build_tools()

    async def _load_spec(self, spec):
        if isinstance(spec, dict):
            self._spec = spec
        elif isinstance(spec, str) and spec.startswith(("http://", "https://")):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=5.0), follow_redirects=True
            ) as c:
                r = await c.get(spec)
                self._spec = r.json()
        else:
            self._spec = json.loads(Path(spec).read_text())
        self._build_index()

    def _build_index(self):
        self._index = []
        self._by_method = {m: [] for m in self._METHODS}
        self._word_index = {}
        for path, methods in self._spec.get("paths", {}).items():
            for method, details in methods.items():
                if method in self._SKIP or not isinstance(details, dict):
                    continue
                m = method.upper()
                summary = details.get("summary", "")
                entry = (m, path, summary.lower(), details)
                idx = len(self._index)
                self._index.append(entry)
                if m in self._by_method:
                    self._by_method[m].append(idx)
                tokens = set()
                for seg in path.lower().split("/"):
                    seg = seg.strip("{}")
                    if seg:
                        tokens.add(seg)
                for word in summary.lower().split():
                    if len(word) > 1:
                        tokens.add(word)
                for token in tokens:
                    self._word_index.setdefault(token, []).append(idx)

    def _deref(self, obj):
        if not isinstance(obj, dict) or "$ref" not in obj:
            return obj
        ref = obj["$ref"]
        if ref not in self._ref_cache:
            parts = ref.lstrip("#/").split("/")
            node = self._spec
            for p in parts:
                node = node[p]
            self._ref_cache[ref] = node
        return self._ref_cache[ref]

    async def _request(self, method, path, **kwargs):
        if kwargs.get("files"):
            kwargs["files"] = {
                k: tuple(v) if isinstance(v, list) else v
                for k, v in kwargs["files"].items()
            }
        r = await self._client.request(method, path, **kwargs)
        try:
            data = r.json()
        except Exception:
            data = r.text or None
        return {"status": r.status_code, "ok": r.is_success, "data": data}

    async def get(self, path, params=None):
        return await self._request("GET", path, params=params)

    async def post(self, path, json=None, data=None, files=None):
        return await self._request("POST", path, json=json, data=data, files=files)

    async def put(self, path, json=None, data=None, files=None):
        return await self._request("PUT", path, json=json, data=data, files=files)

    async def patch(self, path, json=None, data=None, files=None):
        return await self._request("PATCH", path, json=json, data=data, files=files)

    async def delete(self, path, params=None):
        return await self._request("DELETE", path, params=params)

    def search(self, query) -> list | dict:
        if not self._spec:
            return {"error": "No OpenAPI spec loaded"}
        q = query.strip()
        if not q:
            return []
        method_filter = None
        parts = q.split(None, 1)
        if parts[0] in self._METHODS:
            method_filter = parts[0]
            q = parts[1] if len(parts) > 1 else "*"

        regex = None
        if re.search(r"[|\\()\[\]{}^$+]", q):
            try:
                regex = re.compile(q, re.IGNORECASE)
            except re.error:
                pass

        if regex:
            candidates = range(len(self._index))
        elif method_filter and q == "*":
            candidates = self._by_method.get(method_filter, [])
        elif "*" not in q and "?" not in q and not method_filter:
            candidates = self._keyword_candidates(q)
        else:
            if method_filter:
                candidates = self._by_method.get(method_filter, [])
            else:
                candidates = range(len(self._index))

        results = []
        for idx in candidates:
            m, path, summary_lower, details = self._index[idx]
            if method_filter and m != method_filter:
                continue
            if regex:
                if not (
                    regex.search(path) or regex.search(summary_lower) or regex.search(m)
                ):
                    continue
            elif "*" in q or "?" in q:
                if not fnmatch.fnmatch(path, q):
                    continue
            elif q.lower() not in f"{path} {summary_lower}":
                continue
            results.append(self._build_result(m, path, details))
        return results

    def _keyword_candidates(self, query):
        q = query.lower()
        if q in self._word_index:
            return self._word_index[q]
        candidates = set()
        for word, indices in self._word_index.items():
            if q in word:
                candidates.update(indices)
        return candidates if candidates else range(len(self._index))

    def _build_result(self, method, path, details):
        params = {}
        for p in details.get("parameters", []):
            p = self._deref(p)
            if isinstance(p, dict):
                req = " (required)" if p.get("required") else ""
                schema = self._deref(p.get("schema", {}))
                ptype = schema.get("type", "?") if isinstance(schema, dict) else "?"
                params[p.get("name", "?")] = f"{ptype}{req}"
        body = {}
        rb = self._deref(details.get("requestBody", {}))
        if isinstance(rb, dict):
            json_schema = self._deref(
                rb.get("content", {}).get("application/json", {}).get("schema", {})
            )
            if isinstance(json_schema, dict):
                for k, v in json_schema.get("properties", {}).items():
                    v = self._deref(v)
                    body[k] = v.get("type", "?") if isinstance(v, dict) else "?"
        result = {"method": method, "path": path, "summary": details.get("summary", "")}
        if params:
            result["params"] = params
        if body:
            result["body"] = body
        return result

    def _pbx_searchable(self):
        items = dict(self._pbx_tool_index or {})
        for method, path, _, details in self._index:
            key = f"{method} {path}"
            items[key] = SearchItem(
                leaf=path.split("/")[-1].strip("{}"),
                desc=details.get("summary", ""),
                params=[
                    p.get("name", "")
                    for p in details.get("parameters", [])
                    if isinstance(p, dict)
                ],
            )
        return items

    async def _pbx_close(self):
        if self._client:
            await self._client.aclose()

    @property
    def __doc__(self):
        base = str(self._client.base_url) if self._client else (self._base_url or "?")
        title = self._spec.get("info", {}).get("title", "") if self._spec else ""
        header = f"HTTP API: {title} ({base})" if title else f"HTTP API: {base}"
        if self._index:
            header += f" — {len(self._index)} endpoints"
        lines = [
            f"{header}.",
            "Methods: get(path, params=), post(path, json=), put(path, json=), patch(path, json=), delete(path).",
        ]
        if self._spec:
            lines.append(
                "Use search(pattern) to find endpoints by path, method, or summary."
            )
        return "\n".join(lines)
