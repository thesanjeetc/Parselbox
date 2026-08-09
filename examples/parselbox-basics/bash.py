"""
Bash Examples - Shell commands inside the sandbox.

bash(command) executes shell commands and returns stdout as a string.
Raises RuntimeError on non-zero exit with stderr.

- Working directory is /workspace
- Each call is isolated (cd, export don't persist), but filesystem changes do
- Python and bash share the same filesystem
"""

import asyncio
from textwrap import dedent

from rich.console import Console

from parselbox import Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_basics():
    section("Bash Basics")

    async with Parselbox() as sbx:
        await sbx.execute_code('bash("echo hello world")')
        await sbx.execute_code('bash("date +%Y-%m-%d")')
        await sbx.execute_code('bash("pwd")')


async def demo_pipes():
    section("Pipes & Text Processing")

    async with Parselbox() as sbx:
        await sbx.execute_code("""bash("echo -e 'cherry\\napple\\nbanana' | sort")""")
        await sbx.execute_code(
            """bash("echo -e 'a\\nb\\na\\nc\\nb\\na' | sort | uniq -c | sort -rn")"""
        )
        await sbx.execute_code(
            """bash("echo -e '10\\n20\\n30' | awk '{sum+=$1} END{print sum}'")"""
        )


async def demo_shared_filesystem():
    section("Shared Filesystem (Python <-> Bash)")

    async with Parselbox() as sbx:
        await sbx.execute_code('bash("echo from_bash > shared.txt")')
        await sbx.execute_code('open("shared.txt").read().strip()')

        await sbx.execute_code(
            dedent("""
            with open('from_py.txt', 'w') as f:
                f.write('from_python')
        """)
        )
        await sbx.execute_code('bash("cat from_py.txt")')


async def demo_file_ops():
    section("File Operations")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            bash("echo 'name,age,city' > data.csv")
            bash("echo 'Alice,30,NYC' >> data.csv")
            bash("echo 'Bob,25,LA' >> data.csv")
            bash("echo 'Charlie,35,Chicago' >> data.csv")
            bash("cat data.csv | tail -n +2 | cut -d, -f1")
        """)
        )
        await sbx.execute_code(
            dedent("""
            import csv
            list(csv.DictReader(open('data.csv')))
        """)
        )


async def demo_errors():
    section("Error Handling")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            try:
                bash("echo 'something failed' >&2; exit 1")
            except RuntimeError as e:
                f"caught: {e}"
        """)
        )
        await sbx.execute_code(
            dedent("""
            try:
                bash("nonexistent_command")
            except RuntimeError as e:
                f"caught: {e}"
        """)
        )


async def demo_grep_find():
    section("Grep & Find")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            bash("mkdir -p src")
            bash("echo 'def hello(): pass' > src/main.py")
            bash("echo 'def world(): pass' > src/utils.py")
            bash("echo '# TODO: fix this' >> src/utils.py")
        """)
        )
        await sbx.execute_code("""bash("grep -rn 'def ' src/")""")
        await sbx.execute_code("""bash("find src -name '*.py'")""")
        await sbx.execute_code("""bash("grep -rn 'TODO' src/")""")


async def main():
    await demo_basics()
    await demo_pipes()
    await demo_shared_filesystem()
    await demo_file_ops()
    await demo_errors()
    await demo_grep_find()


if __name__ == "__main__":
    asyncio.run(main())
