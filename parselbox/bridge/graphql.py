import re

import httpx

from parselbox.bridge import Bridge
from parselbox.models import SearchItem

OPS_QUERY = (
    "{ __schema {"
    " queryType { fields { name description args { name } type { name kind ofType { name } } } }"
    " mutationType { fields { name description args { name } type { name kind ofType { name } } } }"
    " } }"
)

_TYPE_FRAGMENT = (
    '{alias}: __type(name: "{name}") {{'
    " kind name description"
    " inputFields {{ name description type {{ name kind ofType {{ name kind ofType {{ name }} }} }} }}"
    " fields {{ name description type {{ name kind ofType {{ name kind ofType {{ name }} }} }} }}"
    " }}"
)


def _type_name(t):
    if not t:
        return "?"
    if t.get("kind") in ("NON_NULL", "LIST"):
        inner = _type_name(t.get("ofType"))
        return f"{inner}!" if t["kind"] == "NON_NULL" else f"[{inner}]"
    return t.get("name") or "?"


class GraphQLBridge(Bridge):
    _pbx_type = "graphql"

    def __init__(self, url, token=None, headers=None, auth=None):
        self._url = url
        self._headers = {"Content-Type": "application/json", **(headers or {})}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._auth = auth
        self._operations = {}
        self._type_cache = {}
        self._client = None

    async def _pbx_connect(self):
        self._client = httpx.AsyncClient(
            headers=self._headers,
            auth=self._auth,
            timeout=httpx.Timeout(60.0, connect=5.0),
        )
        try:
            resp = await self._client.post(self._url, json={"query": OPS_QUERY})
        except httpx.HTTPError as e:
            await self._client.aclose()
            self._client = None
            raise ConnectionError(f"Failed to connect to {self._url}: {e}") from e
        try:
            result = resp.json()
            schema = result["data"]["__schema"]
        except (KeyError, ValueError):
            msg = ""
            try:
                msg = result.get("errors", [{}])[0].get("message", "")
            except Exception:
                pass
            raise ConnectionError(
                msg or f"Introspection failed (HTTP {resp.status_code})"
            )

        for root_key, op_type in [("queryType", "query"), ("mutationType", "mutation")]:
            root = schema.get(root_key)
            if not root:
                continue
            for field in root.get("fields", []):
                self._operations[field["name"]] = {
                    "op": op_type,
                    "description": field.get("description") or "",
                    "args": [a["name"] for a in field.get("args", [])],
                    "returns": _type_name(field.get("type")),
                }

        await super()._pbx_connect()

    @property
    def __doc__(self):
        return (
            f"GraphQL API: {self._url}.\n"
            "graphql(query, variables=) to execute queries and mutations.\n"
            "search(pattern) to find operations by name, args, or return type.\n"
            "type(names) to introspect input/output types."
        )

    async def _pbx_close(self):
        if self._client:
            await self._client.aclose()
        self._type_cache = {}
        await super()._pbx_close()

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query string.
            variables: Optional variables dict.

        Returns:
            The data from the GraphQL response, or {errors, data} on failure.
        """
        body = {"query": query}
        if variables:
            body["variables"] = variables
        resp = await self._client.post(self._url, json=body)
        data = resp.json()
        if "errors" in data:
            return {"errors": data["errors"], "data": data.get("data")}
        return data.get("data")

    async def type(self, names: str | list[str]) -> dict:
        """Introspect one or more GraphQL types to discover their fields.

        Args:
            names: Type name or list of type names (e.g. "IssueCreateInput" or ["IssueCreateInput", "IssueUpdateInput"]).

        Returns:
            Dict of type name -> {kind, fields: [{name, type, description}]}.
            For a single name, returns the type info directly.
        """
        single = isinstance(names, str)
        if single:
            names = [names]

        to_fetch = [n for n in names if n not in self._type_cache]
        if to_fetch:
            fragments = " ".join(
                _TYPE_FRAGMENT.format(alias=n, name=n) for n in to_fetch
            )
            result = await self.graphql(f"{{ {fragments} }}")
            if not isinstance(result, dict):
                data = {}
            elif "errors" in result:
                data = result.get("data") or {}
            else:
                data = result
            for n in to_fetch:
                raw = data.get(n)
                if not raw:
                    self._type_cache[n] = {"error": f"Type '{n}' not found"}
                    continue
                raw_fields = raw.get("inputFields") or raw.get("fields") or []
                self._type_cache[n] = {
                    "kind": raw["kind"],
                    "fields": [
                        {
                            "name": f["name"],
                            "type": _type_name(f.get("type")),
                            **(
                                {"description": f["description"]}
                                if f.get("description")
                                else {}
                            ),
                        }
                        for f in raw_fields
                    ],
                }

        result = {
            n: self._type_cache.get(n, {"error": f"Type '{n}' not found"})
            for n in names
        }
        return result[names[0]] if single else result

    def search(self, pattern: str) -> list:
        """Search GraphQL operations by name, description, arguments, or return type.

        Prefix with "query" or "mutation" to filter by operation type (e.g. "mutation create").

        Args:
            pattern: Regex pattern to match against operations.

        Returns:
            List of matching operations with name, type, description, args, and return type.
        """
        q = pattern.strip()
        if not q:
            return []

        type_filter = None
        parts = q.split(None, 1)
        if parts[0].lower() in ("query", "mutation"):
            type_filter = parts[0].lower()
            q = parts[1] if len(parts) > 1 else ""

        if q:
            try:
                regex = re.compile(q, re.IGNORECASE)
                match = lambda t: bool(regex.search(t))
            except re.error:
                p = q.lower()
                match = lambda t: p in t.lower()
        else:
            match = lambda t: True

        results = []
        for name, op in self._operations.items():
            if type_filter and op["op"] != type_filter:
                continue
            text = f"{name} {op['description']} {' '.join(op['args'])} {op['returns']}"
            if match(text):
                results.append(
                    {
                        "name": name,
                        "type": op["op"],
                        "description": op["description"][:120],
                        "args": op["args"],
                        "returns": op["returns"],
                    }
                )
        return results

    def _pbx_searchable(self):
        return {
            name: SearchItem(
                leaf=name,
                desc=op["description"][:120],
                params=op["args"],
                fields=[op["op"], op["returns"]],
            )
            for name, op in self._operations.items()
        }
