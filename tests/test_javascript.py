"""Tests for js() / require() / Python-JS interop."""

from textwrap import dedent

import pytest

from parselbox import Parselbox

pytestmark = pytest.mark.asyncio


async def run(sbx, code):
    result = await sbx.execute_code(dedent(code).strip())
    if not result.is_success:
        raise AssertionError(f"Execution failed: {result.error}")
    return result


class TestJs:
    async def test_basic_types(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx, "js(\"return {n: 42, s: 'hi', b: true, a: [1,2], o: {x:1}}\")"
            )
            assert r.output == {
                "n": 42,
                "s": "hi",
                "b": True,
                "a": [1, 2],
                "o": {"x": 1},
            }

    async def test_kwargs(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                'js("return data.map(x => x * n)", data=[1, 2, 3], n=10)',
            )
            assert r.output == [10, 20, 30]

    async def test_stateless(self):
        async with Parselbox() as sbx:
            await run(sbx, 'js("var x = 42")')
            r = await run(sbx, 'js("return typeof x")')
            assert r.output == "undefined"

    async def test_async(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const p = new Promise(r => setTimeout(() => r(42), 10));
                    return await p;
                ''')
            """,
            )
            assert r.output == 42

    async def test_error(self):
        async with Parselbox() as sbx:
            r = await sbx.execute_code('js("return undefined_var.prop")')
            assert not r.is_success


class TestJsCallbacks:
    async def test_auto_proxy(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                result = js('''
                    const filtered = data.filter(pred);
                    return filtered.map(transform);
                ''',
                    data=[1, 2, 3, 4, 5, 6],
                    pred=lambda x, *_: x % 2 == 0,
                    transform=lambda x, *_: x * 100,
                )
                result
            """,
            )
            assert r.output == [200, 400, 600]

    async def test_method_callback(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                class Scorer:
                    def __init__(self):
                        self.calls = 0
                    def score(self, x, *_):
                        self.calls += 1
                        return x * 10
                s = Scorer()
                result = js("return items.map(fn)", items=[1, 2, 3], fn=s.score)
                [result, s.calls]
            """,
            )
            assert r.output == [[10, 20, 30], 3]

    async def test_callback_error_propagates(self):
        async with Parselbox() as sbx:
            r = await sbx.execute_code(
                dedent(
                    """
                def bad_fn(x, *_):
                    raise ValueError("nope")
                js("return items.map(fn)", items=[1], fn=bad_fn)
            """
                ).strip()
            )
            assert not r.is_success
            assert "nope" in r.error


class TestRequire:
    async def test_proxy_and_auto_inject(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                lodash.chunk([1, 2, 3, 4, 5, 6], 2)
            """,
            )
            assert r.output == [[1, 2], [3, 4], [5, 6]]

            r = await run(sbx, 'js("return lodash.sum(data)", data=[10, 20, 30])')
            assert r.output == 60

    async def test_alias_explicit(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                yaml = require("js-yaml", alias="yaml")
                yaml.load("key: value")
            """,
            )
            assert r.output == {"key": "value"}

    async def test_alias_auto_npm(self):
        """Package names with dots/dashes auto-generate camelCase alias."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                yaml = require("js-yaml")
                yaml.load("a: 1")
            """,
            )
            assert r.output == {"a": 1}

            r = await run(sbx, 'js("return jsYaml.load(s)", s="x: 2")')
            assert r.output == {"x": 2}

    async def test_alias_auto_local(self):
        """Local files auto-generate alias from filename stem."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                with open("my-utils.ts", "w") as f:
                    f.write('export function add(a: number, b: number) { return a + b; }')
            """,
            )
            r = await run(
                sbx,
                """
                mod = require("./my-utils.ts")
                mod.add(3, 4)
            """,
            )
            assert r.output == 7

    async def test_import_inside_js(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const { marked } = await import("npm:marked");
                    return marked("# Hello");
                ''')
            """,
            )
            assert "<h1>" in r.output

    async def test_npm_scoped(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                mp = require("@msgpack/msgpack")
                type(mp).__name__
            """,
            )
            assert r.output == "ParselboxJsProxy"


class TestRequireFrozen:
    async def test_default_blocks_require(self):
        async with Parselbox() as sbx:
            r = await sbx.execute_code('require("lodash")')
            assert not r.is_success

    async def test_default_blocks_npm_with_network(self):
        async with Parselbox(network=True) as sbx:
            r = await sbx.execute_code('require("lodash")')
            assert not r.is_success

    async def test_startup_packages_allowed(self):
        async with Parselbox(packages=["npm:lodash"]) as sbx:
            r = await run(sbx, 'require("lodash").sum([1, 2, 3])')
            assert r.output == 6

    async def test_startup_blocks_others(self):
        async with Parselbox(packages=["npm:lodash"]) as sbx:
            r = await sbx.execute_code('require("marked")')
            assert not r.is_success

    async def test_auto_load_allows_runtime(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(sbx, 'require("lodash").sum([1, 2, 3])')
            assert r.output == 6

    async def test_startup_with_python_packages(self):
        async with Parselbox(packages=["numpy", "npm:lodash"]) as sbx:
            r = await run(
                sbx,
                """
                import numpy as np
                data = np.array([10, 20, 30]).tolist()
                js("return lodash.sum(data)", data=data)
            """,
            )
            assert r.output == 60


class TestProxyReturnConversion:
    async def test_list_returns(self):
        """Arrays return as Python lists, not dicts."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                r = {}
                r["chunk"] = lodash.chunk([1,2,3,4,5,6], 2)
                r["sortBy"] = lodash.sortBy([{"x":3},{"x":1},{"x":2}], "x")
                r["flatten"] = lodash.flattenDeep([[1,[2]],[3,[4,[5]]]])
                r["uniq"] = lodash.uniq([1,1,2,2,3])
                r
            """,
            )
            assert r.output["chunk"] == [[1, 2], [3, 4], [5, 6]]
            assert r.output["sortBy"] == [{"x": 1}, {"x": 2}, {"x": 3}]
            assert r.output["flatten"] == [1, 2, 3, 4, 5]
            assert r.output["uniq"] == [1, 2, 3]

    async def test_dict_returns(self):
        """Plain objects return as regular Python dicts."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                merged = lodash.merge({"a": {"b": 1}}, {"a": {"c": 2}})
                [type(merged).__name__, merged["a"]["b"], merged["a"]["c"]]
            """,
            )
            assert r.output == ["dict", 1, 2]

    async def test_primitive_returns(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                mathjs = require("mathjs")
                [lodash.sum([1,2,3]), mathjs.det([[1,2],[3,4]]), mathjs.sqrt(144)]
            """,
            )
            assert r.output == [6, -2, 12]

    async def test_live_object_stays_proxy(self):
        """Class instances with methods stay as ParselboxJsProxy, not converted to dict."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                dayjs = require("dayjs")
                d = dayjs("2026-06-15")
                [type(d).__name__, d.format("YYYY-MM-DD"), d.year(), d.add(30, "day").format("YYYY-MM-DD")]
            """,
            )
            assert r.output == ["ParselboxJsProxy", "2026-06-15", 2026, "2026-07-15"]

    async def test_groupby_type_field(self):
        """groupBy on a field named 'type' returns correct dict keys (was bug #13)."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                data = [{"type": "a", "v": 1}, {"type": "b", "v": 2}, {"type": "a", "v": 3}]
                grouped = lodash.groupBy(data, "type")
                sorted(grouped.keys())
            """,
            )
            assert r.output == ["a", "b"]


class TestProxyDictAccess:
    async def test_subscript_access(self):
        """All dict keys accessible via subscript, including collision names."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                d = lodash.merge({}, {"values": [1,2,3], "type": "test", "items": 42})
                [d["values"], d["type"], d["items"]]
            """,
            )
            assert r.output == [[1, 2, 3], "test", 42]

    async def test_json_serializable(self):
        """Returned dicts serialize cleanly."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                import json
                lodash = require("lodash")
                d = lodash.merge({}, {"values": [1,2], "type": "test"})
                json.loads(json.dumps(d))
            """,
            )
            assert r.output == {"values": [1, 2], "type": "test"}


class TestProxyDictArgs:
    async def test_lodash_with_dict_args(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                r = {}
                r["invert"] = lodash.invert({"a": "1", "b": "2"})
                r["pick"] = lodash.pick({"a": 1, "b": 2, "c": 3}, ["a", "c"])
                r["omit"] = lodash.omit({"a": 1, "b": 2, "c": 3}, ["b"])
                r["merge"] = lodash.merge({"x": {"a": 1}}, {"x": {"b": 2}})
                r
            """,
            )
            assert r.output["invert"] == {"1": "a", "2": "b"}
            assert r.output["pick"] == {"a": 1, "c": 3}
            assert r.output["omit"] == {"a": 1, "c": 3}
            assert r.output["merge"]["x"] == {"a": 1, "b": 2}


class TestProxyCallbacks:
    async def test_subscript_access(self):
        """Callback items auto-convert: x['key'] works (was bug — only x.key worked)."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                data = [{"name": "alice", "salary": 120000}, {"name": "bob", "salary": 80000}]
                [d["name"] for d in lodash.sortBy(data, lambda x, *_: x["salary"])]
            """,
            )
            assert r.output == ["bob", "alice"]

    async def test_callback_operations(self):
        """All lodash callback operations work with subscript access."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                data = [
                    {"name": "alice", "type": "eng", "salary": 120000},
                    {"name": "bob", "type": "mgr", "salary": 180000},
                    {"name": "carol", "type": "eng", "salary": 95000},
                ]
                r = {}
                r["filter"] = [d["name"] for d in lodash.filter(data, lambda x,*_: x["salary"] > 100000)]
                r["find"] = lodash.find(data, lambda x,*_: x["name"] == "bob")["name"]
                r["partition"] = [len(p) for p in lodash.partition(data, lambda x,*_: x["type"] == "eng")]
                r["reduce"] = lodash.reduce(data, lambda acc,x,*_: acc + x["salary"], 0)
                r["every"] = lodash.every(data, lambda x,*_: x["salary"] > 0)
                r["some"] = lodash.some(data, lambda x,*_: x["salary"] > 150000)
                r["map"] = lodash.map(data, lambda x,*_: x["name"].upper())
                r["countBy"] = lodash.countBy(data, lambda x,*_: x["type"])
                r
            """,
            )
            assert r.output["filter"] == ["alice", "bob"]
            assert r.output["find"] == "bob"
            assert r.output["partition"] == [2, 1]
            assert r.output["reduce"] == 395000
            assert r.output["every"] is True
            assert r.output["some"] is True
            assert r.output["map"] == ["ALICE", "BOB", "CAROL"]
            assert r.output["countBy"] == {"eng": 2, "mgr": 1}

    async def test_callback_error_propagates(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await sbx.execute_code(
                dedent("""
                lodash = require("lodash")
                lodash.map([1,2,3], lambda x, *_: 1/0)
            """).strip()
            )
            assert not r.is_success
            assert "ZeroDivisionError" in r.error

    async def test_callback_side_effects(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                collected = []
                lodash.forEach([1,2,3], lambda x, *_: collected.append(x * 10))
                collected
            """,
            )
            assert r.output == [10, 20, 30]


class TestProxyChain:
    async def test_chain_with_lambdas(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                data = [
                    {"name": "alice", "salary": 120000},
                    {"name": "bob", "salary": 80000},
                    {"name": "carol", "salary": 150000},
                ]
                lodash(data).filter(lambda x,*_: x["salary"] > 100000).sortBy(lambda x,*_: -x["salary"]).map("name").value()
            """,
            )
            assert r.output == ["carol", "alice"]

    async def test_chain_multi_lambda(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                lodash([5,3,1,4,2]).filter(lambda x,*_: x > 2).sortBy(lambda x,*_: -x).map(lambda x,*_: x * 10).value()
            """,
            )
            assert r.output == [50, 40, 30]


class TestProxyToProxy:
    async def test_mathjs_matrices(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                mathjs = require("mathjs")
                A = mathjs.matrix([[1,2],[3,4]])
                B = mathjs.matrix([[5,6],[7,8]])
                r = {}
                r["mul"] = mathjs.multiply(A, B).toArray()
                r["add"] = mathjs.add(A, B).toArray()
                r["transpose"] = mathjs.transpose(A).toArray()
                r["inv"] = mathjs.inv(A).toArray()
                r["det"] = mathjs.det(A)
                r["chained"] = mathjs.multiply(mathjs.transpose(A), B).toArray()
                r
            """,
            )
            assert r.output["mul"] == [[19, 22], [43, 50]]
            assert r.output["add"] == [[6, 8], [10, 12]]
            assert r.output["transpose"] == [[1, 3], [2, 4]]
            assert r.output["det"] == -2
            assert r.output["chained"] == [[26, 30], [38, 44]]

    async def test_dayjs_comparison(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                dayjs = require("dayjs")
                d1 = dayjs("2026-01-15")
                d2 = dayjs("2026-07-04")
                [d1.diff(d2, "day"), d1.isBefore(d2), d2.isAfter(d1), d1.isSame(dayjs("2026-01-15"))]
            """,
            )
            assert r.output[0] == -170
            assert r.output[1] is True
            assert r.output[2] is True
            assert r.output[3] is True

    async def test_zod_schema_composition(self):
        """Zod schemas used as values in zod.object (proxy as arg)."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                zod = require("zod")
                addr = zod.object({"street": zod.string().min(1), "zip": zod.string().min(1)})
                person = zod.object({"home": addr, "work": addr, "name": zod.string().min(1)})
                r = {}
                r["valid"] = person.safeParse({"name": "test", "home": {"street": "1 Main", "zip": "94102"}, "work": {"street": "2 Oak", "zip": "94103"}})["success"]
                r["invalid"] = person.safeParse({"name": "test", "home": {"street": "", "zip": ""}, "work": {"street": "ok", "zip": "ok"}})["success"]
                r
            """,
            )
            assert r.output["valid"] is True
            assert r.output["invalid"] is False

    async def test_threejs_nested_constructors(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                three = require("three")
                box = three.Box3(three.Vector3(-1,-1,-1), three.Vector3(1,1,1))
                [box.min.x, box.max.x, box.containsPoint(three.Vector3(0,0,0))]
            """,
            )
            assert r.output == [-1, 1, True]


class TestProxyConstructors:
    async def test_fuse_js(self):
        """Class constructor auto-detected, Reflect.construct used."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                fuse = require("fuse.js")
                index = fuse([{"title": "Python Guide"}, {"title": "JavaScript Deep Dive"}], {"keys": ["title"]})
                [type(index).__name__, len(index.search("python"))]
            """,
            )
            assert r.output[0] == "ParselboxJsProxy"
            assert r.output[1] >= 1

    async def test_color(self):
        """Color: constructor + method chaining + proxy-to-proxy mix."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                color = require("color")
                red = color("red")
                [type(red).__name__, red.hex(), red.darken(0.5).hex(), red.mix(color("blue"), 0.5).hex()]
            """,
            )
            assert r.output[0] == "ParselboxJsProxy"
            assert r.output[1] == "#FF0000"
            assert r.output[2] == "#800000"
            assert r.output[3] == "#800080"

    async def test_semver_instance(self):
        """SemVer coerce returns live object with .major, .minor, .toString()."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                semver = require("semver")
                v = semver.coerce("3.2")
                [type(v).__name__, v.toString(), v.major, v.minor]
            """,
            )
            assert r.output == ["ParselboxJsProxy", "3.2.0", 3, 2]

    async def test_es5_constructor(self):
        """ES5 function constructor → undefined + prototype heuristic."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                ep = require("expr-eval")
                ep.Parser().parse("x^2").evaluate({"x": 7})
            """,
            )
            assert r.output == 49

    async def test_static_methods_on_class(self):
        """Class with static methods preserved via ParselboxJsProxy wrapping."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                luxon = require("luxon")
                [luxon.DateTime.now().year, luxon.DateTime.fromISO("2026-06-15").toISODate()]
            """,
            )
            assert r.output[0] >= 2026
            assert r.output[1] == "2026-06-15"

    async def test_multiple_classes_one_lib(self):
        """Three.js: many classes from single require."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                three = require("three")
                [three.Vector3(1,2,3).length(), type(three.Scene()).__name__, three.Color(0xff0000).getHexString()]
            """,
            )
            assert round(r.output[0], 2) == 3.74
            assert r.output[1] == "ParselboxJsProxy"
            assert r.output[2] == "ff0000"


class TestProxyCustomModules:
    async def test_ts_module(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                with open("math-utils.ts", "w") as f:
                    f.write('export function add(a: number, b: number) { return a + b; }\\n')
                    f.write('export function mul(a: number, b: number) { return a * b; }\\n')
            """,
            )
            r = await run(
                sbx,
                """
                m = require("./math-utils.ts")
                [m.add(3, 4), m.mul(5, 6)]
            """,
            )
            assert r.output == [7, 30]

    async def test_ts_class_instance(self):
        """TS class instances keep methods, data methods return dicts."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                with open("vec.ts", "w") as f:
                    f.write('''
                export class Vec {
                    constructor(public x: number, public y: number) {}
                    add(other: Vec): Vec { return new Vec(this.x + other.x, this.y + other.y); }
                    mag(): number { return Math.sqrt(this.x**2 + this.y**2); }
                    toArray(): number[] { return [this.x, this.y]; }
                }
                export function createVec(x: number, y: number): Vec { return new Vec(x, y); }
                export function distance(a: Vec, b: Vec): number {
                    return Math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2);
                }
                ''')
            """,
            )
            r = await run(
                sbx,
                """
                vec = require("./vec.ts")
                v1 = vec.createVec(3, 4)
                v2 = vec.createVec(1, 2)
                [v1.mag(), v1.add(v2).toArray(), vec.distance(v1, v2), v1.toArray()]
            """,
            )
            assert r.output[0] == 5
            assert r.output[1] == [4, 6]
            assert r.output[3] == [3, 4]

    async def test_ts_hot_reload(self):
        """Overwriting a TS file and re-requiring picks up changes."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                with open("version.ts", "w") as f:
                    f.write('export function ver() { return "v1"; }')
            """,
            )
            r = await run(sbx, 'require("./version.ts").ver()')
            assert r.output == "v1"

            await run(
                sbx,
                """
                with open("version.ts", "w") as f:
                    f.write('export function ver() { return "v2"; }')
            """,
            )
            r = await run(sbx, 'require("./version.ts").ver()')
            assert r.output == "v2"


class TestProxyEdgeCases:
    async def test_empty_inputs(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                [lodash.groupBy([], "x"), lodash.sortBy([], "x"), lodash.filter([], lambda x,*_: True), lodash.chunk([], 2)]
            """,
            )
            assert r.output == [{}, [], [], []]

    async def test_unicode(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                sorted(lodash.groupBy([{"type": "日本語"}, {"type": "中文"}, {"type": "日本語"}], "type").keys())
            """,
            )
            assert r.output == ["中文", "日本語"]

    async def test_deep_nesting(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                d = lodash.set({}, "a.b.c.d.e.f", 42)
                lodash.get(d, "a.b.c.d.e.f")
            """,
            )
            assert r.output == 42

    async def test_large_arrays(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                mathjs = require("mathjs")
                big = list(range(5000))
                [len(lodash.chunk(big, 100)), len(lodash.uniq(big + big)), mathjs.sum(big)]
            """,
            )
            assert r.output == [50, 5000, 12497500]

    async def test_null_handling(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                lodash.compact([1, None, 0, False, "", 2, 3])
            """,
            )
            assert r.output == [1, 2, 3]

    async def test_statefulness(self):
        """Variables and require'd modules persist across executions."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(sbx, "lodash = require('lodash')")
            await run(sbx, "MY_DATA = [1, 2, 3]")
            r = await run(sbx, "lodash.sum(MY_DATA)")
            assert r.output == 6

    async def test_error_recovery(self):
        """Proxy still works after errors."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(sbx, "lodash = require('lodash')")
            r1 = await sbx.execute_code("lodash.map([1,2,3], lambda x, *_: 1/0)")
            assert not r1.is_success
            r2 = await run(sbx, "lodash.sum([1,2,3])")
            assert r2.output == 6


class TestCrossPackagePipeline:
    async def test_lodash_mathjs_dayjs_zod(self):
        """Data flows through 4 packages via proxy, no js() calls."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                mathjs = require("mathjs")
                dayjs = require("dayjs")
                zod = require("zod")

                data = [
                    {"name": "alice", "salary": 120000, "type": "eng"},
                    {"name": "bob", "salary": 80000, "type": "mgr"},
                    {"name": "carol", "salary": 150000, "type": "eng"},
                ]

                # lodash groups
                grouped = lodash.groupBy(data, "type")

                # mathjs stats per group
                stats = {}
                for t in grouped:
                    sals = lodash.map(grouped[t], "salary")
                    stats[t] = round(float(mathjs.mean(sals)))

                # zod validates
                schema = zod.object({"name": zod.string().min(1), "salary": zod.number().positive()})
                valid = sum(1 for d in data if schema.safeParse(d)["success"])

                # dayjs timestamp
                ts = dayjs().format("YYYY")

                {"stats": stats, "valid": valid, "year": ts}
            """,
            )
            assert r.output["stats"]["eng"] == 135000
            assert r.output["stats"]["mgr"] == 80000
            assert r.output["valid"] == 3
            assert int(r.output["year"]) >= 2026


class TestJsNpmImport:
    async def test_zod(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const { z } = await import("npm:zod");
                    const schema = z.object({ name: z.string(), age: z.number().positive() });
                    return { good: schema.safeParse({ name: "Alice", age: 30 }).success,
                             bad: schema.safeParse({ name: "", age: -5 }).success };
                ''')
            """,
            )
            assert r.output["good"] is True
            assert r.output["bad"] is False

    async def test_cheerio(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const cheerio = await import("npm:cheerio");
                    const $ = cheerio.load(html);
                    return { title: $("h1").text(), items: $("li").length };
                ''', html="<h1>Test</h1><ul><li>a</li><li>b</li><li>c</li></ul>")
            """,
            )
            assert r.output["title"] == "Test"
            assert r.output["items"] == 3

    async def test_jsonpath(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const { JSONPath } = await import("npm:jsonpath-plus");
                    return JSONPath({ path: "$.users[*].name", json: data });
                ''', data={"users": [{"name": "Alice"}, {"name": "Bob"}]})
            """,
            )
            assert r.output == ["Alice", "Bob"]


class TestWasm:
    async def test_sqlite(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                records = [{"id": i, "val": i * 10} for i in range(5)]
                js('''
                    const initSqlJs = (await import("npm:sql.js")).default;
                    const db = new (await initSqlJs()).Database();
                    db.run("CREATE TABLE t (id INT, val INT)");
                    const stmt = db.prepare("INSERT INTO t VALUES (?,?)");
                    for (const r of records) stmt.run([r.id, r.val]);
                    stmt.free();
                    const result = db.exec("SELECT SUM(val) FROM t");
                    db.close();
                    return result[0].values[0][0];
                ''', records=records)
            """,
            )
            assert r.output == 100

    async def test_lua_calls_python(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                def multiply(a, b, *_):
                    return float(a) * float(b)

                js('''
                    const { LuaFactory } = await import("npm:wasmoon");
                    const lua = await (new LuaFactory()).createEngine();
                    lua.global.set("mul", pyFn);
                    await lua.doString("result = mul(6, 7)");
                    const val = lua.global.get("result");
                    lua.global.close();
                    return val;
                ''', pyFn=multiply)
            """,
            )
            assert r.output == 42


class TestResolvePath:
    async def test_absolute_and_relative(self, tmp_path):
        async with Parselbox(output_dir=str(tmp_path)) as sbx:
            r = await run(
                sbx,
                """
                js('''
                    return {
                        abs: resolvePath("/files/test.csv"),
                        rel: resolvePath("data.csv"),
                        dotdot: resolvePath("../files/data.csv"),
                    };
                ''')
            """,
            )
            assert r.output["abs"].endswith("/files/test.csv")
            assert r.output["rel"] == str(tmp_path / "data.csv")
            assert r.output["dotdot"].endswith("/files/data.csv")


class TestDenoStreams:
    async def test_stream_with_callback(self, tmp_path):
        async with Parselbox(network=True, output_dir=str(tmp_path)) as sbx:
            await run(
                sbx,
                """
                with open("/workspace/data.csv", "w") as f:
                    f.write("id,value\\n")
                    for i in range(100):
                        f.write(f"{i},{i * 10}\\n")
            """,
            )
            r = await run(
                sbx,
                """
                total = [0]
                def accum(line, *_):
                    parts = str(line).split(",")
                    if len(parts) >= 2:
                        try: total[0] += float(parts[1])
                        except: pass

                NL = chr(10)
                js('''
                    const file = await Deno.open(resolvePath("data.csv"), { read: true });
                    const reader = file.readable.pipeThrough(new TextDecoderStream()).getReader();
                    let buf = "", hdr = false;
                    while (true) {
                        const { done, value } = await reader.read();
                        if (done) break;
                        buf += value;
                        let idx;
                        while ((idx = buf.indexOf(sep)) !== -1) {
                            const line = buf.slice(0, idx);
                            buf = buf.slice(idx + 1);
                            if (!hdr) { hdr = true; continue; }
                            if (line) fn(line);
                        }
                    }
                    return "done";
                ''', fn=accum, sep=NL)

                total[0]
            """,
            )
            assert r.output == 49500.0


class TestEnv:
    async def test_custom_env_vars(self):
        async with Parselbox(env={"MY_VAR": "hello123"}) as sbx:
            r = await run(sbx, "js(\"return Deno.env.get('MY_VAR')\")")
            assert r.output == "hello123"

    async def test_env_does_not_override_internals(self):
        async with Parselbox(env={"DENO_DIR": "/tmp/evil"}) as sbx:
            r = await run(sbx, "js(\"return Deno.env.get('DENO_DIR')\")")
            assert r.output != "/tmp/evil"


class TestSecurity:
    async def test_permissions(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                js('''
                    const run = await Deno.permissions.query({ name: "run" });
                    const ffi = await Deno.permissions.query({ name: "ffi" });
                    try { Deno.readTextFileSync("/etc/passwd"); var read = "allowed"; }
                    catch(e) { var read = e.name; }
                    return { run: run.state, ffi: ffi.state, read_etc: read };
                ''')
            """,
            )
            assert r.output["run"] == "denied"
            assert r.output["ffi"] == "denied"
            assert r.output["read_etc"] == "NotCapable"


class TestPipeline:
    async def test_python_js_roundtrip(self):
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                records = [{"name": f"item_{i}", "value": i * 3} for i in range(10)]
                result = js('''
                    return data.filter(r => r.value > 15).map(r => r.name);
                ''', data=records)
                result
            """,
            )
            assert "item_6" in r.output
            assert "item_1" not in r.output


class TestDirectJsImport:
    async def test_from_js_import(self):
        async with Parselbox() as sbx:
            r = await run(
                sbx,
                """
                from js import Math, Date, Deno
                [Math.PI > 3.14, Date.new(2026, 2, 14).getFullYear(), "parselbox" in Deno.env.get("DENO_DIR")]
            """,
            )
            assert r.output == [True, 2026, True]


class TestProxyAsync:
    async def test_async_function(self):
        """Async JS function auto-resolved via run_sync."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                with open("async_mod.ts", "w") as f:
                    f.write('''
                        export async function slowAdd(a: number, b: number): Promise<number> {
                            await new Promise(r => setTimeout(r, 50));
                            return a + b;
                        }
                    ''')
            """,
            )
            r = await run(
                sbx,
                """
                mod = require("./async_mod.ts")
                mod.slowAdd(10, 20)
            """,
            )
            assert r.output == 30


class TestProxyBridgeInterop:
    async def test_bridge_data_to_proxy(self):
        """Data from Python code flows through proxy and back."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                # Python generates data, proxy processes it
                data = [{"name": f"item_{i}", "value": i * 3} for i in range(10)]
                lodash.sum(lodash.map(data, "value"))
            """,
            )
            assert r.output == 135

    async def test_proxy_result_to_python(self):
        """Proxy results usable in Python operations."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            r = await run(
                sbx,
                """
                lodash = require("lodash")
                mathjs = require("mathjs")
                # Proxy computes, Python uses result
                grouped = lodash.groupBy([{"t":"a","v":1},{"t":"b","v":2},{"t":"a","v":3}], "t")
                total = sum(mathjs.sum(lodash.map(grouped[k], "v")) for k in grouped)
                total
            """,
            )
            assert r.output == 6


class TestProxyFiles:
    async def test_cross_ecosystem_documents(self):
        """JS creates doc → Python reads it back."""
        async with Parselbox(allow_runtime_packages=True) as sbx:
            await run(
                sbx,
                """
                js('''
                    const { PDFDocument } = await import("npm:pdf-lib");
                    const doc = await PDFDocument.create();
                    doc.addPage();
                    const bytes = await doc.save();
                    await Deno.writeFile(resolvePath("test.pdf"), bytes);
                    return bytes.length;
                ''')
            """,
            )
            r = await run(
                sbx,
                """
                import pypdf
                len(pypdf.PdfReader("test.pdf").pages)
            """,
            )
            assert r.output == 1
