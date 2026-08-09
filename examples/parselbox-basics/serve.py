"""
Serve Examples - Dynamic web apps with the serve feature.

Features:
- Static file serving (HTML, CSS, JS at /)
- API handlers (@api.get/post/put/delete at /api/*)
- File uploads (POST /_upload -> /files/)
- Live reload (automatic on file changes)
- Context tools accessible from handlers
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_static_files():
    section("Static File Serving")

    async with Parselbox(serve=8080, packages=["requests"]) as sbx:
        await sbx.execute_code(
            dedent("""
            with open("index.html", "w") as f:
                f.write('<h1>Hello from Parselbox!</h1>')

            with open("style.css", "w") as f:
                f.write('h1 { color: blue; }')

            "files created"
        """)
        )
        await sbx.execute_code(
            dedent("""
            import requests
            r = requests.get("http://localhost:8080/")
            f"GET /: {r.status_code}, {r.text[:40]}"
        """)
        )


async def demo_api_handlers():
    section("API Handlers")

    async with Parselbox(serve=8081, packages=["requests"]) as sbx:
        await sbx.execute_code(
            dedent("""
            items = [{"id": 1, "name": "Item 1"}, {"id": 2, "name": "Item 2"}]

            @api.get("/items")
            def list_items(params):
                limit = int(params.get("limit", 10))
                return items[:limit]

            @api.post("/items")
            def create_item(body):
                new_item = {"id": len(items) + 1, "name": body["name"]}
                items.append(new_item)
                return new_item

            @api.delete("/items")
            def delete_item(body):
                for i, item in enumerate(items):
                    if item["id"] == body["id"]:
                        return {"deleted": items.pop(i)}
                return {"error": "not found"}

            "API registered"
        """)
        )
        await sbx.execute_code(
            dedent("""
            import requests
            requests.get("http://localhost:8081/api/items").json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.post("http://localhost:8081/api/items", json={"name": "Item 3"}).json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.delete("http://localhost:8081/api/items", json={"id": 2}).json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.get("http://localhost:8081/api/items").json()
        """)
        )


async def demo_context_in_handlers():
    section("Context Tools in Handlers")

    class Database:
        def __init__(self):
            self._data = {"users": [{"id": 1, "name": "Alice"}]}

        def query(self, table: str) -> list:
            return self._data.get(table, [])

        def insert(self, table: str, record: dict) -> dict:
            if table not in self._data:
                self._data[table] = []
            record["id"] = len(self._data[table]) + 1
            self._data[table].append(record)
            return record

    async with Parselbox(
        serve=8082, context={"db": Database()}, packages=["requests"]
    ) as sbx:
        await sbx.execute_code(
            dedent("""
            @api.get("/users")
            def get_users(params):
                return db.query("users")

            @api.post("/users")
            def create_user(body):
                return db.insert("users", {"name": body["name"]})

            "handlers registered"
        """)
        )
        await sbx.execute_code(
            dedent("""
            import requests
            requests.get("http://localhost:8082/api/users").json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.post("http://localhost:8082/api/users", json={"name": "Bob"}).json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.get("http://localhost:8082/api/users").json()
        """)
        )


async def demo_file_upload():
    section("File Upload")

    async with Parselbox(serve=8083, packages=["requests"]) as sbx:
        await sbx.execute_code(
            dedent("""
            with open("test_upload.txt", "w") as f:
                f.write("Hello from uploaded file!")
            "test file created"
        """)
        )
        await sbx.execute_code(
            dedent("""
            import requests
            with open("test_upload.txt", "rb") as f:
                r = requests.post(
                    "http://localhost:8083/_upload",
                    files={"file": ("uploaded.txt", f)}
                )
            r.json()
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.get("http://localhost:8083/files/uploaded.txt").text
        """)
        )


async def demo_live_reload():
    section("Live Reload")

    async with Parselbox(serve=8084, packages=["requests"]) as sbx:
        await sbx.execute_code(
            dedent("""
            with open("index.html", "w") as f:
                f.write("<h1>Version 1</h1>")
            "v1"
        """)
        )
        await sbx.execute_code(
            dedent("""
            import requests
            requests.get("http://localhost:8084/").text
        """)
        )
        await sbx.execute_code(
            dedent("""
            with open("index.html", "w") as f:
                f.write("<h1>Version 2</h1>")
            "v2 (live reload triggered)"
        """)
        )
        await sbx.execute_code(
            dedent("""
            requests.get("http://localhost:8084/").text
        """)
        )


async def main():
    await demo_static_files()
    await demo_api_handlers()
    await demo_context_in_handlers()
    await demo_file_upload()
    await demo_live_reload()


if __name__ == "__main__":
    asyncio.run(main())
