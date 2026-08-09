import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from textwrap import dedent

import pytest

from parselbox import Parselbox
from parselbox.bridge import ShellBridge, HTTPBridge, GraphQLBridge

pytestmark = pytest.mark.asyncio


class _HTTPHandler(BaseHTTPRequestHandler):
    def _respond(self, body, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else None

    def do_GET(self):
        self._respond({"method": "GET", "path": self.path})

    def do_POST(self):
        self._respond({"method": "POST", "path": self.path, "body": self._read_body()})

    def do_PUT(self):
        self._respond({"method": "PUT", "path": self.path, "body": self._read_body()})

    def do_PATCH(self):
        self._respond({"method": "PATCH", "path": self.path, "body": self._read_body()})

    def do_DELETE(self):
        self._respond({"method": "DELETE", "path": self.path})

    def log_message(self, *_):
        pass


class _GraphQLHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        query = body.get("query", "")

        if "__schema" in query:
            result = {
                "data": {
                    "__schema": {
                        "queryType": {
                            "fields": [
                                {
                                    "name": "users",
                                    "description": "List all users",
                                    "args": [{"name": "limit"}],
                                    "type": {
                                        "name": "UserList",
                                        "kind": "OBJECT",
                                        "ofType": None,
                                    },
                                },
                                {
                                    "name": "user",
                                    "description": "Get user by ID",
                                    "args": [{"name": "id"}],
                                    "type": {
                                        "name": "User",
                                        "kind": "OBJECT",
                                        "ofType": None,
                                    },
                                },
                            ]
                        },
                        "mutationType": {
                            "fields": [
                                {
                                    "name": "createUser",
                                    "description": "Create a new user",
                                    "args": [{"name": "name"}, {"name": "email"}],
                                    "type": {
                                        "name": "User",
                                        "kind": "OBJECT",
                                        "ofType": None,
                                    },
                                },
                                {
                                    "name": "deleteUser",
                                    "description": "Delete a user",
                                    "args": [{"name": "id"}],
                                    "type": {
                                        "name": "Boolean",
                                        "kind": "SCALAR",
                                        "ofType": None,
                                    },
                                },
                            ]
                        },
                    }
                }
            }
        elif "__type" in query:
            result = {
                "data": {
                    "User": {
                        "kind": "OBJECT",
                        "name": "User",
                        "description": "A user",
                        "inputFields": None,
                        "fields": [
                            {
                                "name": "id",
                                "description": "User ID",
                                "type": {
                                    "name": "ID",
                                    "kind": "SCALAR",
                                    "ofType": None,
                                },
                            },
                            {
                                "name": "name",
                                "description": "Full name",
                                "type": {
                                    "name": "String",
                                    "kind": "SCALAR",
                                    "ofType": None,
                                },
                            },
                        ],
                    }
                }
            }
        else:
            variables = body.get("variables", {})
            if "createUser" in query:
                result = {
                    "data": {
                        "createUser": {"id": "3", "name": variables.get("name", "new")}
                    }
                }
            else:
                result = {
                    "data": {
                        "users": [
                            {"id": "1", "name": "Alice"},
                            {"id": "2", "name": "Bob"},
                        ]
                    }
                }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, *_):
        pass


def _start_server(handler_class):
    server = HTTPServer(("127.0.0.1", 0), handler_class)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", server


@pytest.fixture
def http_url():
    url, server = _start_server(_HTTPHandler)
    yield url
    server.shutdown()


@pytest.fixture
def graphql_url():
    url, server = _start_server(_GraphQLHandler)
    yield url
    server.shutdown()


SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test", "version": "1.0"},
    "paths": {
        "/users": {
            "get": {
                "summary": "List users",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}}
                ],
            },
            "post": {
                "summary": "Create user",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                            }
                        }
                    }
                },
            },
        },
        "/users/{id}": {
            "get": {
                "summary": "Get user by ID",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            },
            "delete": {
                "summary": "Delete user",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            },
        },
        "/orders": {
            "get": {"summary": "List orders"},
        },
    },
}


class TestShellBridge:
    async def test_exec_stdout_and_exit_code(self):
        sh = ShellBridge()
        await sh._pbx_connect()
        result = await sh.exec("echo hello world")
        await sh._pbx_close()
        assert result["exit_code"] == 0
        assert "hello world" in result["output"]
        assert result["lines"] >= 1

    async def test_exec_failing_command(self):
        sh = ShellBridge()
        await sh._pbx_connect()
        result = await sh.exec("exit 42")
        await sh._pbx_close()
        assert result["exit_code"] == 42

    async def test_exec_stderr_in_output(self):
        sh = ShellBridge()
        await sh._pbx_connect()
        result = await sh.exec("echo err >&2")
        await sh._pbx_close()
        assert "err" in result["output"]

    async def test_exec_multiline(self):
        sh = ShellBridge()
        await sh._pbx_connect()
        result = await sh.exec("for i in 1 2 3; do echo $i; done")
        await sh._pbx_close()
        assert result["lines"] == 3

    async def test_connect_bad_command_raises(self):
        sh = ShellBridge("false")
        with pytest.raises(ConnectionError, match="not reachable"):
            await sh._pbx_connect()

    async def test_integration_via_parselbox(self):
        async with Parselbox(context={"sh": ShellBridge()}) as sandbox:
            result = await sandbox.execute_code('sh.exec(command="echo integration")')
            assert result.is_success
            assert result.output["exit_code"] == 0
            assert "integration" in result.output["output"]

    async def test_exec_with_log_streaming(self):
        async with Parselbox(context={"sh": ShellBridge()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                task = sh.exec.task(command="for i in $(seq 1 5); do echo line$i; done")
                s = await task.wait(timeout=5)
                {"state": s.state, "lines": s.result["lines"], "tail": task.tail(3)}
            """)
            )
            assert result.is_success
            assert result.output["state"] == "done"
            assert result.output["lines"] == 5
            assert "line" in result.output["tail"]

    async def test_interactive_shell(self):
        async with Parselbox(context={"sh": ShellBridge()}) as sandbox:
            result = await sandbox.execute_code(
                dedent("""
                import asyncio
                task = sh.shell.task()
                await asyncio.sleep(0.3)
                task.send("echo from_interactive")
                await asyncio.sleep(0.3)
                task.send("exit 0")
                s = await task.wait(timeout=5)
                {"state": s.state, "tail": task.tail(5)}
            """)
            )
            assert result.is_success
            assert result.output["state"] == "done"
            assert "from_interactive" in result.output["tail"]


class TestHTTPBridge:
    async def test_get(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = await bridge.get("/test")
        await bridge._pbx_close()
        assert result["ok"] is True
        assert result["data"]["method"] == "GET"

    async def test_post(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = await bridge.post("/items", json={"name": "widget"})
        await bridge._pbx_close()
        assert result["ok"] is True
        assert result["data"]["body"]["name"] == "widget"

    async def test_put(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = await bridge.put("/items/1", json={"name": "updated"})
        await bridge._pbx_close()
        assert result["data"]["method"] == "PUT"
        assert result["data"]["body"]["name"] == "updated"

    async def test_patch(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = await bridge.patch("/items/1", json={"status": "done"})
        await bridge._pbx_close()
        assert result["data"]["method"] == "PATCH"

    async def test_delete(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = await bridge.delete("/items/1")
        await bridge._pbx_close()
        assert result["data"]["method"] == "DELETE"

    async def test_token_sets_auth_header(self, http_url):
        bridge = HTTPBridge(base_url=http_url, token="secret123")
        await bridge._pbx_connect()
        assert bridge._headers["Authorization"] == "Bearer secret123"
        await bridge._pbx_close()

    async def test_search_keyword(self, http_url):
        bridge = HTTPBridge(base_url=http_url, spec=SPEC)
        await bridge._pbx_connect()
        results = bridge.search("user")
        await bridge._pbx_close()
        assert len(results) >= 3
        assert any(r["path"] == "/users" for r in results)

    async def test_search_method_filter(self, http_url):
        bridge = HTTPBridge(base_url=http_url, spec=SPEC)
        await bridge._pbx_connect()
        results = bridge.search("DELETE *")
        await bridge._pbx_close()
        assert all(r["method"] == "DELETE" for r in results)
        assert len(results) == 1

    async def test_search_regex(self, http_url):
        bridge = HTTPBridge(base_url=http_url, spec=SPEC)
        await bridge._pbx_connect()
        results = bridge.search("user|order")
        await bridge._pbx_close()
        paths = [r["path"] for r in results]
        assert "/users" in paths
        assert "/orders" in paths

    async def test_search_no_spec(self, http_url):
        bridge = HTTPBridge(base_url=http_url)
        await bridge._pbx_connect()
        result = bridge.search("anything")
        await bridge._pbx_close()
        assert "error" in result

    async def test_no_base_url_no_spec_raises(self):
        bridge = HTTPBridge()
        with pytest.raises(ValueError, match="base_url"):
            await bridge._pbx_connect()

    async def test_searchable_includes_spec_endpoints(self, http_url):
        bridge = HTTPBridge(base_url=http_url, spec=SPEC)
        await bridge._pbx_connect()
        items = bridge._pbx_searchable()
        await bridge._pbx_close()
        assert any("user" in k.lower() for k in items)


class TestGraphQLBridge:
    async def test_connect_introspects(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        assert "users" in bridge._operations
        assert "createUser" in bridge._operations
        assert bridge._operations["users"]["op"] == "query"
        assert bridge._operations["createUser"]["op"] == "mutation"
        await bridge._pbx_close()

    async def test_query(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        result = await bridge.graphql("{ users { id name } }")
        await bridge._pbx_close()
        assert result["users"][0]["name"] == "Alice"

    async def test_query_with_variables(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        result = await bridge.graphql(
            "mutation($name: String!) { createUser(name: $name) { id name } }",
            variables={"name": "Charlie"},
        )
        await bridge._pbx_close()
        assert result["createUser"]["name"] == "Charlie"

    async def test_type_introspection(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        result = await bridge.type("User")
        await bridge._pbx_close()
        assert result["kind"] == "OBJECT"
        field_names = [f["name"] for f in result["fields"]]
        assert "id" in field_names
        assert "name" in field_names

    async def test_type_cached(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        await bridge.type("User")
        assert "User" in bridge._type_cache
        result = await bridge.type("User")
        assert result["kind"] == "OBJECT"
        await bridge._pbx_close()

    async def test_type_batch(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        result = await bridge.type(["User"])
        await bridge._pbx_close()
        assert "User" in result

    async def test_search_all(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        results = bridge.search("user")
        await bridge._pbx_close()
        names = [r["name"] for r in results]
        assert "users" in names
        assert "createUser" in names

    async def test_search_mutation_filter(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        results = bridge.search("mutation create")
        await bridge._pbx_close()
        assert all(r["type"] == "mutation" for r in results)
        assert any(r["name"] == "createUser" for r in results)

    async def test_search_query_filter(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        results = bridge.search("query user")
        await bridge._pbx_close()
        assert all(r["type"] == "query" for r in results)

    async def test_search_empty(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        assert bridge.search("") == []
        assert bridge.search("   ") == []
        await bridge._pbx_close()

    async def test_token_sets_header(self, graphql_url):
        bridge = GraphQLBridge(graphql_url, token="tok123")
        assert bridge._headers["Authorization"] == "Bearer tok123"

    async def test_searchable(self, graphql_url):
        bridge = GraphQLBridge(graphql_url)
        await bridge._pbx_connect()
        items = bridge._pbx_searchable()
        await bridge._pbx_close()
        assert "users" in items
        assert "createUser" in items

    async def test_connect_failure(self):
        bridge = GraphQLBridge("http://127.0.0.1:1")
        with pytest.raises(ConnectionError):
            await bridge._pbx_connect()
