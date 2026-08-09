import asyncio
import base64
import builtins
import datetime as _dt
import importlib.util
import json
import os
import re
import sys
import time as _time
import traceback

import site as _site

import pyodide_js
from js import JSON as _JSON, ArrayBuffer as _ArrayBuffer, Object, Reflect
from pyodide.code import find_imports, run_js
from pyodide.ffi import create_proxy, run_sync, to_js

_PBX_BYTES_KEY = "__pbx_bytes__"

_id = builtins.id
_str = builtins.str
_repr = builtins.repr
_type = builtins.type
_hasattr = builtins.hasattr
_getattr = builtins.getattr
_isinstance = builtins.isinstance
_NONE_TYPE = _type(None)
_INF = builtins.float("inf")

_pbx_binary_replacer = run_js("""
(key, value) => {
    if (value instanceof ArrayBuffer || ArrayBuffer.isView(value)) {
        const b = value instanceof ArrayBuffer
            ? new Uint8Array(value)
            : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
        let s = "";
        for (let i = 0; i < b.length; i += 0x8000) {
            s += String.fromCharCode(...b.subarray(i, i + 0x8000));
        }
        return { "__pbx_bytes__": btoa(s) };
    }
    return value;
}
""")


def _pbx_bytes_hook(d):
    if len(d) == 1 and _PBX_BYTES_KEY in d:
        return base64.b64decode(d[_PBX_BYTES_KEY])
    return d


class ParselboxRpc:
    @staticmethod
    def _json_default(o):
        if _hasattr(o, "to_py"):
            try:
                return o.to_py()
            except Exception:
                pass
        if _hasattr(o, "__json__"):
            return o.__json__()
        if _isinstance(o, (bytes, bytearray)):
            return {"__b64__": base64.b64encode(o).decode()}
        return _repr(o)

    @staticmethod
    def _b64_hook(d):
        if len(d) == 1 and "__b64__" in d:
            return base64.b64decode(d["__b64__"])
        return d

    @staticmethod
    async def call(payload_dict):
        payload_str = json.dumps(payload_dict, default=ParselboxRpc._json_default)
        result_str = await _pbx_rpc(payload_str)
        result_obj = json.loads(result_str, object_hook=ParselboxRpc._b64_hook)
        if _isinstance(result_obj, dict) and "__error__" in result_obj:
            error_type_name = result_obj.get("__error_type__", "Exception")
            error_message = result_obj.get(
                "__error__", "An unknown error occurred in the host callback."
            )
            exception_class = _getattr(builtins, error_type_name, RuntimeError)
            raise exception_class(error_message) from None
        return result_obj

    @staticmethod
    def serialize(obj) -> str:
        def _walk(obj, ancestors=None):
            if _hasattr(obj, "to_py"):
                try:
                    obj = obj.to_py()
                except Exception:
                    return {"type": "not_serializable", "repr": _repr(obj)}

            if _isinstance(obj, (_str, int, bool, _NONE_TYPE)):
                return obj
            if _isinstance(obj, float):
                if obj != obj:
                    return None
                if obj == _INF or obj == -_INF:
                    return None
                return obj

            if ancestors is None:
                ancestors = set()

            obj_id = _id(obj)
            if obj_id in ancestors:
                return {"type": "circular_reference", "repr": _repr(obj)}

            next_ancestors = ancestors | {obj_id}

            if _isinstance(obj, (list, tuple, set, frozenset)):
                return [_walk(item, next_ancestors) for item in obj]

            if _hasattr(obj, "__json__"):
                return _walk(obj.__json__(), next_ancestors)

            if _isinstance(obj, dict):
                return {
                    _str(key): _walk(value, next_ancestors)
                    for key, value in obj.items()
                }

            if _isinstance(obj, (_dt.date, _dt.datetime)):
                return obj.isoformat()

            return {"type": "not_serializable", "repr": _repr(obj)}

        return json.dumps(_walk(obj))


class ParselboxJsProxy:
    __slots__ = ("_js",)

    def __init__(self, js_obj):
        object.__setattr__(self, "_js", js_obj)

    def __getattr__(self, name):
        val = getattr(object.__getattribute__(self, "_js"), name)
        if callable(val):
            if ParselboxJsProxy._has_prototype_methods(val):
                return ParselboxJsProxy(val)
            return lambda *a, **kw: ParselboxJsProxy._call_js(val, a, kw)
        if ParselboxJsProxy._is_js_binary(val):
            return ParselboxJsProxy._binary_to_bytes(val)
        if type(val).__name__ == "JsProxy":
            return ParselboxJsProxy(val)
        return val.to_py() if hasattr(val, "to_py") else val

    def __call__(self, *args, **kwargs):
        return ParselboxJsProxy._call_js(
            object.__getattribute__(self, "_js"), args, kwargs
        )

    def __dir__(self):
        js = object.__getattribute__(self, "_js")
        try:
            return list(Object.keys(js).to_py())
        except Exception:
            return []

    def __repr__(self):
        return repr(object.__getattribute__(self, "_js"))

    @staticmethod
    def _is_js_binary(v):
        try:
            return bool(_ArrayBuffer.isView(v)) or v.constructor.name == "ArrayBuffer"
        except Exception:
            return False

    @staticmethod
    def _binary_to_bytes(v):
        try:
            return bytes(v.to_py())
        except Exception:
            return json.loads(
                _JSON.stringify(v, _pbx_binary_replacer), object_hook=_pbx_bytes_hook
            )

    @staticmethod
    def _to_py(result):
        if result is None or isinstance(result, (str, int, float, bool, bytes)):
            return result
        if ParselboxJsProxy._is_js_binary(result):
            return ParselboxJsProxy._binary_to_bytes(result)
        try:
            return json.loads(
                _JSON.stringify(result, _pbx_binary_replacer),
                object_hook=_pbx_bytes_hook,
            )
        except Exception:
            try:
                return result.to_py()
            except Exception:
                return result

    @staticmethod
    def _to_js_arg(val):
        if isinstance(val, ParselboxJsProxy):
            return object.__getattribute__(val, "_js")
        if type(val).__name__ == "JsProxy":
            return val
        if isinstance(val, (bytes, bytearray, memoryview)):
            return to_js(val)
        if isinstance(val, dict):
            return to_js(val, dict_converter=Object.fromEntries)
        if isinstance(val, (list, tuple)):
            return to_js(val)
        if callable(val) and not isinstance(val, type):
            return create_proxy(ParselboxJsProxy._wrap_callback(val))
        return val

    @staticmethod
    def _wrap_callback(fn):
        def wrapper(*args):
            return fn(*(a.to_py() if hasattr(a, "to_py") else a for a in args))

        return wrapper

    @staticmethod
    def _has_prototype_methods(fn):
        try:
            props = Object.getOwnPropertyNames(fn.prototype)
            return any(str(p) != "constructor" for p in props)
        except Exception:
            return False

    @staticmethod
    def _convert_result(result):
        if result is None or isinstance(result, (str, int, float, bool, bytes)):
            return result
        if ParselboxJsProxy._is_js_binary(result):
            return ParselboxJsProxy._binary_to_bytes(result)
        if not hasattr(result, "constructor"):
            return ParselboxJsProxy._to_py(result)
        try:
            ctor = result.constructor.name
            if ctor not in ("Object", "Array"):
                return ParselboxJsProxy(result)
            if ctor == "Object" and Object.getPrototypeOf(result) != Object.prototype:
                return ParselboxJsProxy(result)
        except Exception:
            pass
        return ParselboxJsProxy._to_py(result)

    @staticmethod
    def _call_js(fn, args, kwargs):
        js_args = [ParselboxJsProxy._to_js_arg(a) for a in args]
        js_kwargs = {k: ParselboxJsProxy._to_js_arg(v) for k, v in kwargs.items()}
        try:
            result = fn(*js_args, **js_kwargs)
            if type(result).__name__ == "PyodideFuture":
                result = run_sync(result)
            converted = ParselboxJsProxy._convert_result(result)
            if converted is None and ParselboxJsProxy._has_prototype_methods(fn):
                return ParselboxJsProxy(Reflect.construct(fn, to_js(js_args)))
            return converted
        except Exception as e:
            if "cannot be invoked without" in str(e).lower():
                return ParselboxJsProxy(Reflect.construct(fn, to_js(js_args)))
            raise


class ParselboxRuntime:
    _cache = {}

    @staticmethod
    def _to_camel_alias(name):
        name = re.sub(r"^@[^/]+/", "", name)
        parts = re.split(r"[^a-zA-Z0-9]+", name)
        parts = [p for p in parts if p]
        if not parts:
            return None
        result = parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
        return result if result.isidentifier() else None

    @classmethod
    def require(cls, package_name, alias=None):
        """Import npm packages, local JS/TS files, or WASM modules. Returns a wrapped module.

        lodash = require("lodash")
        lodash.chunk([1, 2, 3, 4], 2)          → [[1, 2], [3, 4]]

        dayjs = require("dayjs")
        dayjs("2026-01-01").add(30, "day").format("YYYY-MM-DD")

        t = require("./transform.ts")          → local file, hot-reloads on change

        Method calls auto-convert between Python and JS types. Class constructors auto-detect new.
        Packages are also available inside js() by their name or alias.
        """
        is_local = package_name.startswith("/") or package_name.startswith(".")

        if not alias:
            if package_name.isidentifier():
                alias = package_name
            else:
                source = (
                    os.path.splitext(os.path.basename(package_name))[0]
                    if is_local
                    else package_name
                )
                alias = cls._to_camel_alias(source)
                if not alias:
                    raise ValueError(
                        f"Could not derive alias from '{package_name}'. "
                        f"Provide alias= (e.g. require('{package_name}', alias='myalias'))"
                    )

        if not is_local and package_name in cls._cache:
            _pbx_alias(package_name, alias)
            return cls._cache[package_name]

        key = package_name + "?v=" + str(id(object())) if is_local else package_name
        mod = run_sync(_pbx_import(key, alias))

        if hasattr(mod, "default"):
            try:
                default = mod.default
                if default is not None and not isinstance(
                    default, (int, float, str, bool)
                ):
                    mod = default
            except Exception:
                pass

        if getattr(mod, "_pbx_wasi", False):
            wrapped = cls._wrap_wasi(mod, alias)
        else:
            wrapped = ParselboxJsProxy(mod)
        cls._cache[package_name] = wrapped
        return wrapped

    @staticmethod
    def _wrap_wasi(js_run, name):
        proxy = ParselboxJsProxy(js_run)

        def run(args=None, stdin="", env=None, preopens=None, argv0=None):
            opts = {
                "stdin": stdin,
                "env": env or {},
                "preopens": preopens or {},
                "argv0": argv0 or name,
            }
            return proxy(list(args or []), opts)

        run.__name__ = name
        run.__doc__ = (
            f"WASI command '{name}' — runs sandboxed, in-process.\n\n"
            f"    {name}(args=None, stdin='', env=None, preopens=None, argv0=None)\n"
            "      -> {'exit': int, 'stdout': bytes, 'stderr': str, 'missing': [...]}\n\n"
            "stdin accepts str or bytes. preopens maps guest dirs to sandbox dirs,\n"
            "e.g. preopens={'/usr': 'vendor/usr'}. File access follows sandbox\n"
            "mounts and permissions; unqualified paths resolve to the workspace."
        )
        return run

    @staticmethod
    def js(code, **kwargs):
        """Execute JavaScript with Python variables injected as kwargs.

        js("return data.map(x => x * 2)", data=[1,2,3]) → [2, 4, 6]

        Stateless — each call runs in a fresh scope. Packages from require() available by alias.
        Python callables passed as kwargs are auto-proxied as JS callbacks.
        """
        proxies = []
        converted = {}
        for k, v in kwargs.items():
            if isinstance(v, ParselboxJsProxy):
                converted[k] = object.__getattribute__(v, "_js")
            elif callable(v) and not hasattr(v, "_prevent_proxy"):
                proxy = create_proxy(v)
                proxies.append(proxy)
                converted[k] = proxy
            else:
                converted[k] = v

        try:
            args = to_js(converted, dict_converter=Object.fromEntries)
            result = run_sync(_pbx_eval(code, args))
            return ParselboxJsProxy._to_py(result)
        finally:
            for p in proxies:
                p.destroy()

    @staticmethod
    def bash(command):
        """Execute a shell command. Returns stdout as a string. Raises on non-zero exit with stderr.

        bash("ls /mnt/project")      → list files
        bash("grep -rn TODO src/")   → search code
        bash("cat data.csv | wc -l") → pipes work

        Each call is isolated — cd/export don't persist. Filesystem changes do.
        """
        r = json.loads(run_sync(_pbx_bash(command)))
        if r["exitCode"] != 0 and r["stderr"]:
            raise RuntimeError(f"bash exit {r['exitCode']}: {r['stderr'].strip()}")
        return r["stdout"]

    @staticmethod
    def help(obj=None):
        if obj is None:
            return builtins.sbx.help() if hasattr(builtins, "sbx") else None
        return getattr(obj, "__doc__", None)


class TaskStatus:
    def __init__(self, state, elapsed, message, logfile, result=None, error=None):
        self.state = state
        self.elapsed = elapsed
        self.message = message
        self.logfile = logfile
        self.result = result
        self.error = error
        self.done = state in ("done", "failed", "cancelled")
        self.ok = state == "done"

    def __repr__(self):
        if self.ok:
            return f"TaskStatus(done, {self.elapsed}s, result={repr(self.result)[:80]})"
        return f"TaskStatus({self.state}, {self.elapsed}s, {self.message})"

    def __json__(self):
        msg = (
            self.message[:200] + "..."
            if self.message and len(self.message) > 200
            else self.message
        )
        d = {
            "state": self.state,
            "elapsed": self.elapsed,
            "message": msg,
            "logfile": self.logfile,
        }
        if self.ok:
            d["result"] = self.result
        if self.error:
            d["error"] = self.error
        return d


class ParselboxTask:
    """Background task from .task() calls. Tracks progress, streams logs, supports messaging.

    status()             → TaskStatus snapshot (state, elapsed, message, result/error)
    await wait(timeout=) → TaskStatus after waiting up to timeout seconds
    tail(n=5)            → last n lines from the task log
    send(msg)            → send a message to the running task
    cancel()             → cancel the task
    result()             → return value if done, None otherwise
    await task           → wait until done, returns raw result
    """

    _all = {}

    def __init__(self, coro, name, task_id):
        self.id = task_id
        self.name = name
        self.logfile = f"/workspace/.parselbox/tasks/{task_id}.log"
        self.started_at = _time.time()
        self._task = asyncio.create_task(coro)
        self._task.add_done_callback(lambda _: ParselboxTask._all.pop(self.id, None))
        ParselboxTask._all[self.id] = self

    def done(self):
        return self._task.done()

    def result(self):
        if not self._task.done():
            return None
        try:
            return self._task.result()
        except (asyncio.CancelledError, Exception):
            return None

    def error(self):
        if not self._task.done():
            return None
        if self._task.cancelled():
            return "cancelled"
        try:
            self._task.result()
            return None
        except Exception as e:
            return str(e)

    def status(self):
        if self._task.cancelled():
            state = "cancelled"
        elif self._task.done():
            try:
                self._task.result()
                state = "done"
            except Exception:
                state = "failed"
        else:
            state = "running"
        last = None
        try:
            with open(self.logfile) as f:
                lines = f.read().splitlines()
                if lines:
                    last = lines[-1]
        except FileNotFoundError:
            pass
        return TaskStatus(
            state=state,
            elapsed=round(_time.time() - self.started_at, 1),
            message=last or state,
            logfile=self.logfile,
            result=self.result() if state == "done" else None,
            error=self.error() if state == "failed" else None,
        )

    def tail(self, n=5):
        try:
            with open(self.logfile) as f:
                lines = f.readlines()[-n:]
            return "".join(lines)
        except FileNotFoundError:
            return ""

    def send(self, msg):
        run_sync(
            ParselboxRpc.call(
                {
                    "name": "",
                    "op": "_task_send",
                    "path": [],
                    "args": [self.id, msg],
                    "kwargs": {},
                }
            )
        )

    def cancel(self):
        self._task.cancel()

    async def wait(self, timeout=None):
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            pass
        return self.status()

    def __await__(self):
        return self._task.__await__()

    def __repr__(self):
        s = self.status()
        suffix = f" — {s.message}" if s.message and s.message != s.state else ""
        return f"<Task {self.id} {self.name} [{s.state}] {s.elapsed}s{suffix}>"


class ParselboxNamespace:
    """Remote namespace — methods execute on the host and return results."""

    def __init__(self, root_name, path_parts=None):
        self._root_name = root_name
        self._path_parts = path_parts or []

    @property
    def __doc__(self):
        name = ".".join([self._root_name] + self._path_parts)
        parts = [
            f"Remote namespace '{name}' — methods execute on the host and return results.",
            f"  Call:       {name}.method(args)",
            f"  Background: {name}.method.task(args) → returns a Task object",
            f'  Details:    sbx.inspect("{name}.method") → description, parameters, and types',
            f'  Search:     sbx.search("pattern") → find across all namespaces',
        ]
        try:
            result = run_sync(self._rpc_call("help"))
        except Exception:
            result = None
        if isinstance(result, dict):
            return result
        if result:
            parts += ["", result]
        return "\n".join(parts)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(
                f"'{self._root_name}' object has no attribute '{name}'"
            )
        if name == "task":
            root = self._root_name
            path = self._path_parts

            def _launch(*args, **kwargs):
                task_name = f"{root}.{'.'.join(path)}"
                task_id = f"{root}_{os.urandom(3).hex()}"
                coro = ParselboxRpc.call(
                    {
                        "name": root,
                        "path": path,
                        "op": "call",
                        "args": args,
                        "kwargs": kwargs,
                        "task_id": task_id,
                    }
                )
                return ParselboxTask(coro, task_name, task_id)

            return _launch
        new_path = self._path_parts + [name]
        return ParselboxNamespace(self._root_name, new_path)

    def __json__(self):
        return ".".join([self._root_name] + self._path_parts)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        if not self._path_parts:
            return f"<RemoteNamespace: {self._root_name}>"
        return f"<RemoteTool: {self._root_name}.{'.'.join(self._path_parts)}>"

    async def _rpc_call(self, op, *args, **kwargs):
        return await ParselboxRpc.call(
            {
                "name": self._root_name,
                "path": self._path_parts,
                "op": op,
                "args": args,
                "kwargs": kwargs,
            }
        )

    def __call__(self, *args, **kwargs):
        try:
            return run_sync(self._rpc_call("call", *args, **kwargs))
        except Exception as e:
            raise type(e)(str(e)) from None

    def __dir__(self):
        return run_sync(self._rpc_call("dir"))


class ParselboxPackages:
    SITE_PACKAGES = _site.getsitepackages()[0]

    @staticmethod
    async def install(input_data):
        import micropip

        targets = []

        if hasattr(input_data, "to_py"):
            input_data = input_data.to_py()

        if isinstance(input_data, list):
            targets = input_data
        elif isinstance(input_data, str):
            try:
                targets = find_imports(input_data)
            except SyntaxError:
                return {
                    "installed": [],
                    "failed": [],
                    "error": "SyntaxError parsing code",
                }

        to_install = []
        seen = set()

        try:
            mapping = pyodide_js._api._import_name_to_package_name.to_py()
        except (AttributeError, Exception):
            mapping = {}

        for key in ["numpy", "test"]:
            if key in mapping:
                del mapping[key]

        for name in targets:
            if "://" in name or name.endswith(".whl"):
                to_install.append(name)
                continue

            root = name.split(".")[0]
            if root in seen:
                continue
            seen.add(root)

            if importlib.util.find_spec(root) is not None:
                continue

            if os.path.exists(f"{root}.py") or os.path.isdir(root):
                continue

            pkg_name = mapping.get(root, root)
            to_install.append(pkg_name)

        results = {"installed": [], "failed": []}

        if to_install:
            for pkg in to_install:
                try:
                    await micropip.install(pkg)
                    results["installed"].append(pkg)
                except Exception as e:
                    sys.stderr.write(f"Failed to install {pkg}: {e}\n")
                    results["failed"].append(pkg)

            importlib.invalidate_caches()

        return results


class ParselboxCapture:
    MAX_BUFFER = 1_000_000

    class _Stream:
        def __init__(self, original):
            self.original = original
            self.buffer = []
            self._size = 0

        def write(self, s):
            if s:
                if self._size < ParselboxCapture.MAX_BUFFER:
                    self.buffer.append(s)
                    self._size += len(s)
                self.original.write(s)

        def flush(self):
            self.original.flush()

        def clear(self):
            self.buffer.clear()
            self._size = 0

        def getvalue(self):
            return "".join(self.buffer)

    stdout = None
    stderr = None

    @classmethod
    def init(cls):
        cls.stdout = cls._Stream(sys.stdout)
        cls.stderr = cls._Stream(sys.stderr)
        sys.stdout = cls.stdout
        sys.stderr = cls.stderr

    @classmethod
    def collect(cls):
        out = cls.stdout.getvalue()
        err = cls.stderr.getvalue()
        cls.stdout.clear()
        cls.stderr.clear()
        return out, err


class ParselboxRouter:
    _handlers = {}

    @classmethod
    def _register(cls, path, method):
        def decorator(fn):
            full_path = f"/api{path}" if not path.startswith("/api") else path
            cls._handlers[(method, full_path)] = fn
            return fn

        return decorator

    @classmethod
    def get(cls, path):
        return cls._register(path, "GET")

    @classmethod
    def post(cls, path):
        return cls._register(path, "POST")

    @classmethod
    def put(cls, path):
        return cls._register(path, "PUT")

    @classmethod
    def patch(cls, path):
        return cls._register(path, "PATCH")

    @classmethod
    def delete(cls, path):
        return cls._register(path, "DELETE")

    @classmethod
    async def handle(cls, method, path, params=None, body=None):
        if hasattr(params, "to_py"):
            params = params.to_py()
        if hasattr(body, "to_py"):
            body = body.to_py()
        handler = cls._handlers.get((method.upper(), path))
        if not handler:
            return {"status": 404, "body": {"error": f"No handler for {method} {path}"}}
        try:
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                result = handler(body)
            else:
                result = handler(params)
            if asyncio.iscoroutine(result):
                result = await result
            return {"status": 200, "body": result}
        except Exception as e:
            return {
                "status": 500,
                "body": {"error": str(e), "traceback": traceback.format_exc()},
            }

    @classmethod
    def list_routes(cls):
        return [{"method": m, "path": p} for (m, p) in cls._handlers.keys()]

    @classmethod
    def clear(cls):
        cls._handlers.clear()


def display(content):
    """Render HTML inline in the chat (MCP Apps hosts: Claude Desktop, VS Code).

    display("<h1>Q3</h1><p>Revenue up 12%</p>")   # HTML
    display("report.html")                        # a file in the workspace

    Tailwind and daisyUI are injected, so plain markup is styled without a build
    step; `pbx.call("/api/route", body)` reaches @api handlers when serve= is on.
    One view per execution — the last display() wins.
    """
    from pathlib import Path as _Path

    if _hasattr(content, "__fspath__"):
        content = os.fspath(content)
    if not _isinstance(content, _str):
        raise TypeError(
            f"display() expects HTML or a file path, got {_type(content).__name__}"
        )

    if "<" not in content:
        path = _Path(content)
        if not path.is_absolute():
            path = _Path("/workspace") / path
        if not path.is_file():
            raise FileNotFoundError(
                f"display(): {content!r} is neither HTML nor an existing file"
            )
        content = path.read_text()

    run_sync(
        ParselboxRpc.call(
            {
                "name": "_pbx",
                "op": "_display",
                "path": [],
                "args": [],
                "kwargs": {"html": content},
            }
        )
    )


sys.executable = "/usr/bin/python3"
ParselboxCapture.init()

builtins.require = ParselboxRuntime.require
builtins.js = ParselboxRuntime.js
builtins.bash = ParselboxRuntime.bash
builtins.help = ParselboxRuntime.help
builtins.api = ParselboxRouter
builtins.display = display
