import { loadPyodide, version as PYODIDE_VERSION } from 'pyodide';
import { join } from 'node:path';
import { logger, rpc } from './rpc.ts';
import { analyzeWasm, makeWasiRunner } from './wasi.ts';

const _WasmMemory = WebAssembly.Memory;

async function hashSetup(): Promise<string> {
  const data = new TextEncoder().encode(BOOTSTRAP);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(buf).slice(0, 8)].map((b) =>
    b.toString(16).padStart(2, '0')
  ).join('');
}

function getSnapshotDir(): string {
  return Deno.env.get('DENO_DIR')!;
}

function getSnapshotPath(setupHash: string): string {
  return join(
    getSnapshotDir(),
    `parselbox-${PYODIDE_VERSION}-${setupHash}.snapshot`,
  );
}

async function cleanSnapshots(): Promise<void> {
  const dir = getSnapshotDir();
  for await (const entry of Deno.readDir(dir)) {
    if (
      entry.name.startsWith('parselbox-') && entry.name.endsWith('.snapshot')
    ) {
      try {
        await Deno.remove(join(dir, entry.name));
      } catch {}
    }
  }
}

let crashReject: ((e: Error) => void) | null = null;

export function createCrashPromise() {
  return new Promise<never>((_, reject) => {
    crashReject = reject;
  });
}

globalThis.addEventListener('unhandledrejection', (e) => {
  const msg = (e.reason?.message || String(e.reason)) + '';
  if (msg.includes('KeyboardInterrupt')) {
    e.preventDefault();
    return;
  }
  if (e.reason?.name === 'NotCapable') {
    e.preventDefault();
    return;
  }

  e.preventDefault();
  if (crashReject) {
    crashReject(new Error(`Unhandled rejection: ${msg}`));
  }
});

const BOOTSTRAP = Deno.readTextFileSync(
  new URL('./bootstrap.py', import.meta.url),
);

export async function setupPyodide(
  config: Record<string, any>,
  mounts: Array<[string, string]>,
  env: Record<string, string>,
) {
  const setupHash = await hashSetup();
  const snapshotPath = getSnapshotPath(setupHash);

  let snapshot: Uint8Array | undefined;
  try {
    snapshot = await Deno.readFile(snapshotPath);
  } catch {}

  const maxPages = Math.ceil(((config.memory ?? 2048) * 1024 * 1024) / 65536);
  WebAssembly.Memory = function (descriptor: WebAssembly.MemoryDescriptor) {
    if (!descriptor.maximum || descriptor.maximum > maxPages) {
      descriptor.maximum = maxPages;
    }
    return new _WasmMemory(descriptor);
  } as unknown as typeof WebAssembly.Memory;

  const pyodide = await loadPyodide({
    packageCacheDir: config.package_dir,
    env: env,
    convertNullToNone: true,
    stdout: (msg: string) => logger.debug('pyodide', msg),
    stderr: (msg: string) => logger.warn('pyodide', msg),
    _loadSnapshot: snapshot,
    _makeSnapshot: !snapshot,
  });

  if (!snapshot) {
    await pyodide.runPythonAsync(BOOTSTRAP);
    try {
      await cleanSnapshots();
      snapshot = pyodide.makeMemorySnapshot();
      await Deno.writeFile(snapshotPath, snapshot);
    } catch (_) {}
  }
  if (snapshot) (snapshot.buffer as ArrayBuffer).transfer(0);
  snapshot = undefined;

  const origLoadPackage = pyodide.loadPackage;
  pyodide.loadPackage = (pkgs: any, options: any) =>
    origLoadPackage(pkgs, {
      messageCallback: (msg: string) => logger.debug('pyodide.pip', msg),
      errorCallback: (msg: string) =>
        logger.error('pyodide', `install error: ${msg}`),
      ...options,
    });

  pyodide.FS.mkdirTree('/workspace');
  pyodide.runPython("import os; os.chdir('/workspace')");

  const envSetup = Object.entries(env)
    .map(([k, v]) => `os.environ[${JSON.stringify(k)}] = ${JSON.stringify(v)}`)
    .join('\n');
  if (envSetup) pyodide.runPython(`import os\n${envSetup}`);

  const jsHostRPCBridge = async (payloadStr: string): Promise<string> => {
    const payload = JSON.parse(payloadStr);
    const result = await rpc.call('callback', payload);
    return JSON.stringify(result);
  };

  const loadedPackages = new Map<string, any>();
  const moduleCache = new Map<string, any>();

  const registerAlias = (packageName: string, alias: string) => {
    const mod = moduleCache.get(packageName);
    if (mod) loadedPackages.set(alias, mod);
  };

  const wasmCache = new Map<
    string,
    { stamp: string; module: WebAssembly.Module }
  >();

  const compileWasm = async (hostPath: string): Promise<WebAssembly.Module> => {
    const st = Deno.statSync(hostPath);
    const stamp = `${st.mtime?.getTime() ?? 0}:${st.size}`;
    const hit = wasmCache.get(hostPath);
    if (hit && hit.stamp === stamp) return hit.module;
    const module = await WebAssembly.compile(Deno.readFileSync(hostPath));
    wasmCache.set(hostPath, { stamp, module });
    return module;
  };

  const npmImport = async (packageName: string, alias: string) => {
    const cleanName = packageName.split('?')[0];
    if (cleanName.endsWith('.wasm')) {
      const module = await compileWasm(resolvePath(cleanName));
      const info = analyzeWasm(module);
      if (info.otherNamespaces.length) {
        throw new Error(
          `"${cleanName}" imports from [${
            info.otherNamespaces.join(', ')
          }] — ` +
            `only self-contained and WASI (wasi_snapshot_preview1) modules are supported. ` +
            `Instantiate it in js() with a custom import object instead.`,
        );
      }
      if (info.wasi) {
        if (!info.hasStart) {
          throw new Error(
            `"${cleanName}" is a WASI reactor module (no _start export) — not supported yet. ` +
              `Instantiate it in js() with a custom import object instead.`,
          );
        }
        const name = cleanName
          .split('/')
          .pop()!
          .replace(/\.wasm$/, '');
        const run = makeWasiRunner(module, name, resolvePath, info.wasiNs);
        loadedPackages.set(alias, run);
        return run;
      }
      const instance = new WebAssembly.Instance(module);
      loadedPackages.set(alias, instance.exports);
      return instance.exports;
    }

    const isLocal = packageName.startsWith('/') ||
      packageName.startsWith('./') ||
      packageName.startsWith('../');
    const specifier = isLocal
      ? 'file://' + resolvePath(packageName)
      : `npm:${packageName}`;
    const mod = await import(specifier);
    const resolved = mod.default ?? mod;
    loadedPackages.set(alias, resolved);
    if (!isLocal) moduleCache.set(packageName, resolved);
    return mod;
  };

  const resolvePath = (vfsPath: string): string => {
    if (!vfsPath.startsWith('/')) {
      vfsPath = '/workspace/' + vfsPath;
    }
    const parts: string[] = [];
    for (const p of vfsPath.split('/')) {
      if (p === '..') parts.pop();
      else if (p !== '.' && p !== '') parts.push(p);
    }
    vfsPath = '/' + parts.join('/');

    for (const [vfs, host] of mounts) {
      if (vfsPath === vfs || vfsPath.startsWith(vfs + '/')) {
        return host + vfsPath.slice(vfs.length);
      }
    }
    throw new Error(
      `"${vfsPath}" is in-memory (MEMFS). ` +
        `Read it in Python and pass to js() as a kwarg instead. ` +
        `Note: Deno streams require mounted paths.: ${
          mounts.map(([v]) => v).join(', ') || 'none'
        }`,
    );
  };

  const jsEval = async (code: string, args: Record<string, any>) => {
    try {
      const scope: Record<string, any> = {
        resolvePath,
        ...Object.fromEntries(loadedPackages),
        ...(args && typeof args === 'object' ? args : {}),
      };
      const keys = Object.keys(scope);
      const fn = new Function(
        ...keys,
        `"use strict"; return (async () => { ${code} })();`,
      );
      return await fn(...keys.map((k) => scope[k]));
    } catch (e) {
      throw e instanceof Error ? e : new Error(String(e));
    }
  };

  for (
    const [name, fn] of Object.entries({
      _pbx_rpc: jsHostRPCBridge,
      _pbx_import: npmImport,
      _pbx_alias: registerAlias,
      _pbx_eval: jsEval,
    })
  ) {
    pyodide.globals.set(name, fn);
  }

  return { pyodide, npmImport, compileWasm };
}
