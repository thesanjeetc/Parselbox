"""
Filesystem Examples - Files, mounts, and output handling.

Filesystem layout:
- Working directory (relative paths): Read/Write, persists between calls
- /files/{filename}: Read/write input files
- /mnt/{target}/: Mounted directories (ro or rw)
- output_dir: Persists outputs to host
"""

import asyncio
import tempfile
from pathlib import Path
from textwrap import dedent

from rich.console import Console

from parselbox import Mount, Parselbox

_console = Console(stderr=True)


def section(title):
    _console.print()
    _console.rule(title, style="dim")


async def demo_input_files():
    section("Input Files (/files/)")

    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "data.txt").write_text("Hello from host!\nLine 2\nLine 3")
        Path(tmpdir, "config.json").write_text('{"key": "value", "count": 42}')
        Path(tmpdir, "data.csv").write_text("name,age\nAlice,30\nBob,25")

        async with Parselbox(
            files=[f"{tmpdir}/data.txt", f"{tmpdir}/config.json", f"{tmpdir}/data.csv"]
        ) as sbx:
            await sbx.execute_code("open('/files/data.txt').read()")
            await sbx.execute_code(
                dedent("""
                import json
                json.load(open('/files/config.json'))
            """)
            )
            await sbx.execute_code(
                dedent("""
                import csv
                list(csv.DictReader(open('/files/data.csv')))
            """)
            )
            await sbx.execute_code(
                dedent("""
                import os
                os.listdir('/files')
            """)
            )


async def demo_mounts():
    section("Mounts (/mnt/)")

    with tempfile.TemporaryDirectory() as tmpdir:
        ro_dir = Path(tmpdir) / "readonly"
        ro_dir.mkdir()
        (ro_dir / "reference.txt").write_text("Reference data")
        (ro_dir / "constants.py").write_text("PI = 3.14159")

        rw_dir = Path(tmpdir) / "workspace"
        rw_dir.mkdir()

        async with Parselbox(
            mounts=[
                Mount(host=str(ro_dir), target="data", mode="ro"),
                Mount(host=str(rw_dir), target="work", mode="rw"),
            ]
        ) as sbx:
            await sbx.execute_code("open('/mnt/data/reference.txt').read()")
            await sbx.execute_code(
                dedent("""
                import sys
                sys.path.insert(0, '/mnt/data')
                from constants import PI
                PI
            """)
            )
            await sbx.execute_code(
                dedent("""
                with open('/mnt/work/output.txt', 'w') as f:
                    f.write('Generated!')
                open('/mnt/work/output.txt').read()
            """)
            )
            await sbx.execute_code("open('/mnt/data/test.txt', 'w').write('fail')")


async def demo_working_dir():
    section("Working Directory")

    async with Parselbox() as sbx:
        await sbx.execute_code(
            dedent("""
            with open('report.txt', 'w') as f:
                f.write('Analysis complete')

            import json
            with open('data.json', 'w') as f:
                json.dump({'status': 'ok'}, f)

            'files created'
        """)
        )
        await sbx.execute_code("open('report.txt').read()")


async def demo_output_dir():
    section("Output Directory")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "outputs"
        output_dir.mkdir()

        async with Parselbox(output_dir=str(output_dir)) as sbx:
            await sbx.execute_code(
                dedent("""
                with open('result.txt', 'w') as f:
                    f.write('Final result')
                'saved'
            """)
            )


async def demo_skills():
    section("Skills")

    with tempfile.TemporaryDirectory() as tmpdir:
        skills_dir = Path(tmpdir) / "skills"
        skills_dir.mkdir()

        skill_dir = skills_dir / "data-analysis"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: data-analysis\n"
            "description: Analyze datasets and generate insights\n---\n"
            "# Data Analysis Skill\n\nInstructions for analyzing data..."
        )

        skill_dir2 = skills_dir / "pdf-tools"
        skill_dir2.mkdir()
        (skill_dir2 / "SKILL.md").write_text(
            "---\nname: pdf-tools\n"
            "description: Extract and manipulate PDF documents\n---\n"
            "# PDF Tools"
        )

        async with Parselbox(
            mounts=[Mount(host=str(skills_dir), target="skills")]
        ) as sbx:
            await sbx.execute_code("sbx.info()")
            await sbx.execute_code("bash('ls /mnt/skills/')")
            await sbx.execute_code("bash('cat /mnt/skills/data-analysis/SKILL.md')")


async def main():
    await demo_input_files()
    await demo_mounts()
    await demo_working_dir()
    await demo_output_dir()
    await demo_skills()


if __name__ == "__main__":
    asyncio.run(main())
