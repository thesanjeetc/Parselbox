from textwrap import dedent

import httpx
import pytest

from parselbox import Parselbox
from parselbox.bridge import Bridge

pytestmark = pytest.mark.asyncio


class TestStaticFileServing:
    async def test_serve_static_files(self):
        async with Parselbox(serve=18080) as sandbox:
            await sandbox.execute_code(
                dedent("""
                with open("index.html", "w") as f:
                    f.write("<h1>Hello Parselbox</h1>")
            """)
            )

            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:18080/")
                assert r.status_code == 200
                assert "<h1>Hello Parselbox</h1>" in r.text

    async def test_static_file_served(self):
        async with Parselbox(allow_runtime_packages=True, serve=8099) as sandbox:
            await sandbox.execute_code(
                dedent("""
                with open("test.html", "w") as f:
                    f.write("<h1>Hello</h1>")
            """)
            )
            result = await sandbox.execute_code(
                dedent("""
                import requests
                resp = requests.get("http://localhost:8099/test.html")
                [resp.status_code, "<h1>" in resp.text]
            """)
            )
            assert result.output == [200, True]


class TestAPIHandlers:
    async def test_api_crud_operations(self):
        async with Parselbox(serve=18090) as sandbox:
            await sandbox.execute_code(
                dedent("""
                items = [{"id": 1, "name": "Item 1"}]

                @api.get("/items")
                def list_items(params):
                    limit = int(params.get("limit", 10))
                    return items[:limit]

                @api.post("/items")
                def create_item(body):
                    new_item = {"id": len(items) + 1, "name": body["name"]}
                    items.append(new_item)
                    return new_item

                @api.put("/items")
                def update_item(body):
                    for item in items:
                        if item["id"] == body["id"]:
                            item["name"] = body["name"]
                            return item
                    return {"error": "not found"}

                @api.delete("/items")
                def delete_item(body):
                    for i, item in enumerate(items):
                        if item["id"] == body["id"]:
                            return {"deleted": items.pop(i)}
                    return {"error": "not found"}
            """)
            )

            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:18090/api/items")
                assert r.status_code == 200
                assert len(r.json()) == 1

                r = await client.get("http://localhost:18090/api/items?limit=1")
                assert len(r.json()) == 1

                r = await client.post(
                    "http://localhost:18090/api/items", json={"name": "Item 2"}
                )
                assert r.json()["id"] == 2

                r = await client.put(
                    "http://localhost:18090/api/items",
                    json={"id": 1, "name": "Updated"},
                )
                assert r.json()["name"] == "Updated"

                r = await client.request(
                    "DELETE", "http://localhost:18090/api/items", json={"id": 2}
                )
                assert r.json()["deleted"]["name"] == "Item 2"

    async def test_api_handler_error(self):
        async with Parselbox(serve=18091) as sandbox:
            await sandbox.execute_code(
                dedent("""
                @api.get("/fail")
                def fail_handler(params):
                    raise ValueError("Something went wrong")
            """)
            )

            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:18091/api/fail")
                assert r.status_code == 500
                assert "error" in r.json()

    async def test_api_handler(self):
        async with Parselbox(allow_runtime_packages=True, serve=8098) as sandbox:
            await sandbox.execute_code(
                dedent("""
                @api.get("/ping")
                def ping(params):
                    return {"pong": True}
            """)
            )
            result = await sandbox.execute_code(
                dedent("""
                import requests
                requests.get("http://localhost:8098/api/ping").json()
            """)
            )
            assert result.output == {"pong": True}


class TestContextInHandlers:
    async def test_handler_accesses_context(self):
        class Counter(Bridge):
            def __init__(self):
                self.value = 0

            def increment(self) -> int:
                """Increment counter."""
                self.value += 1
                return self.value

        async with Parselbox(serve=18100, context={"counter": Counter()}) as sandbox:
            await sandbox.execute_code(
                dedent("""
                @api.post("/increment")
                def increment(body):
                    return {"value": counter.increment()}
            """)
            )

            async with httpx.AsyncClient() as client:
                await client.post("http://localhost:18100/api/increment", json={})
                r = await client.post("http://localhost:18100/api/increment", json={})
                assert r.json()["value"] == 2


class TestFileUpload:
    async def test_upload_and_access_file(self):
        async with Parselbox(serve=18110) as sandbox:
            await sandbox.execute_code("None")
            async with httpx.AsyncClient() as client:
                files = {"file": ("test.txt", b"Hello from upload")}
                r = await client.post("http://localhost:18110/_upload", files=files)
                assert r.status_code == 200
                assert r.json()["uploaded"][0]["name"] == "test.txt"

                r = await client.get("http://localhost:18110/files/test.txt")
                assert r.text == "Hello from upload"

            result = await sandbox.execute_code('open("/files/test.txt").read()')
            assert result.output == "Hello from upload"


class TestLiveReload:
    async def test_file_update_reflected(self):
        async with Parselbox(serve=18120) as sandbox:
            await sandbox.execute_code('open("index.html", "w").write("<h1>V1</h1>")')

            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:18120/")
                assert "V1" in r.text

            await sandbox.execute_code('open("index.html", "w").write("<h1>V2</h1>")')

            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:18120/")
                assert "V2" in r.text
