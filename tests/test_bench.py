import asyncio
import statistics
import tempfile
import time
from pathlib import Path

import pytest

from parselbox import Parselbox

pytestmark = pytest.mark.asyncio

STARTUP_ROUNDS = 5
EXEC_ROUNDS = 10
MEMORY_ROUNDS = 5
PKG_ROUNDS = 3

_results = {}


@pytest.fixture(scope="module", autouse=True)
def warmup():
    loop = asyncio.new_event_loop()
    sbx = Parselbox()
    loop.run_until_complete(sbx.connect())
    loop.run_until_complete(sbx.execute_code("1"))
    loop.run_until_complete(sbx.close())
    loop.close()
    yield
    _print_table()


PKG_LIST = ["pandas", "numpy", "matplotlib"]
PKG_VERIFY = "import pandas, numpy, matplotlib; pandas.__version__"


@pytest.fixture(scope="module")
def pkg_cache_dir():
    """Pre-download heavy packages into a cache dir so bench tests only measure loading."""
    tmpdir = tempfile.mkdtemp(prefix="parselbox_bench_pkg_")
    cache = Path(tmpdir)
    loop = asyncio.new_event_loop()
    sbx = Parselbox(packages=PKG_LIST, network=True, package_dir=str(cache))
    loop.run_until_complete(sbx.connect())
    loop.run_until_complete(sbx.execute_code(PKG_VERIFY))
    loop.run_until_complete(sbx.close())
    loop.close()
    yield str(cache)


def _print_table():
    if not _results:
        return
    print("\n")
    print(
        f"  {'Metric':<20} {'Min':>8} {'Max':>8} {'Mean':>8} {'StdDev':>8} {'Threshold':>10} {'Status':>8}"
    )
    print(f"  {'-' * 20} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 8} {'-' * 10} {'-' * 8}")
    for name, (samples, unit, threshold) in _results.items():
        mn = min(samples)
        mx = max(samples)
        mean = statistics.mean(samples)
        stddev = statistics.stdev(samples) if len(samples) > 1 else 0
        status = "PASS" if mean < threshold else "FAIL"
        print(
            f"  {name:<20}"
            f" {mn:>6.0f}{unit:<2}"
            f" {mx:>6.0f}{unit:<2}"
            f" {mean:>6.0f}{unit:<2}"
            f" {stddev:>6.1f}{unit:<2}"
            f" {threshold:>8.0f}{unit:<2}"
            f" {status:>5}"
        )
    print()


class TestPerformanceThresholds:
    async def test_startup(self):
        samples = []
        for _ in range(STARTUP_ROUNDS):
            t0 = time.perf_counter()
            async with Parselbox() as sandbox:
                await sandbox.execute_code("1")
            samples.append((time.perf_counter() - t0) * 1000)
        _results["startup"] = (samples, "ms", 300)
        assert statistics.mean(samples) < 300

    async def test_execution_latency(self):
        async with Parselbox() as sandbox:
            await sandbox.execute_code("1")
            samples = []
            for _ in range(EXEC_ROUNDS):
                t0 = time.perf_counter()
                await sandbox.execute_code("1 + 1")
                samples.append((time.perf_counter() - t0) * 1000)
        _results["execution latency"] = (samples, "ms", 20)
        assert statistics.mean(samples) < 20

    async def test_memory_footprint(self):
        samples = []
        for _ in range(MEMORY_ROUNDS):
            async with Parselbox() as sandbox:
                await sandbox.execute_code("1")
                try:
                    with open(f"/proc/{sandbox._proc.pid}/smaps_rollup") as f:
                        for line in f:
                            if line.startswith("Rss:"):
                                samples.append(int(line.split()[1]) // 1024)
                                break
                except (FileNotFoundError, PermissionError, ValueError):
                    pass

        if samples:
            _results["memory (deno rss)"] = (samples, "MB", 200)
            assert statistics.mean(samples) < 200, (
                f"Deno RSS mean: {statistics.mean(samples):.0f}MB (threshold: 200MB)"
            )
        else:
            pytest.skip("Could not read Deno process memory")

    async def test_package_load_startup(self, pkg_cache_dir):
        """Startup time loading cached heavy packages (pandas/numpy/matplotlib)."""
        samples = []
        for _ in range(PKG_ROUNDS):
            t0 = time.perf_counter()
            async with Parselbox(
                packages=PKG_LIST, package_dir=pkg_cache_dir
            ) as sandbox:
                result = await sandbox.execute_code(PKG_VERIFY)
                assert result.is_success
            samples.append((time.perf_counter() - t0) * 1000)
        _results["pkg load startup"] = (samples, "ms", 5000)
        assert statistics.mean(samples) < 5000

    async def test_package_load_memory(self, pkg_cache_dir):
        """Memory footprint after loading cached heavy packages."""
        samples = []
        for _ in range(PKG_ROUNDS):
            async with Parselbox(
                packages=PKG_LIST, package_dir=pkg_cache_dir
            ) as sandbox:
                await sandbox.execute_code(PKG_VERIFY)
                try:
                    with open(f"/proc/{sandbox._proc.pid}/smaps_rollup") as f:
                        for line in f:
                            if line.startswith("Rss:"):
                                samples.append(int(line.split()[1]) // 1024)
                                break
                except (FileNotFoundError, PermissionError, ValueError):
                    pass

        if samples:
            _results["memory (pkg load)"] = (samples, "MB", 500)
            assert statistics.mean(samples) < 500, (
                f"Pkg load RSS mean: {statistics.mean(samples):.0f}MB (threshold: 500MB)"
            )
        else:
            pytest.skip("Could not read Deno process memory")
