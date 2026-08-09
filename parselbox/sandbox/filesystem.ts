import {
  Bash,
  type IFileSystem,
  InMemoryFs,
  MountableFs,
  ReadWriteFs,
} from 'just-bash';
import { createResolvePath, makeWasiBashCommand } from './wasi.ts';

const WASM_BIN_DIR = 'bin';

export class FileWatcher {
  private listeners: ((path: string) => void)[] = [];
  private changed = new Set<string>();

  notify(path: string) {
    this.changed.add(path);
    for (const fn of this.listeners) fn(path);
  }

  on(fn: (path: string) => void) {
    this.listeners.push(fn);
  }

  clear() {
    this.changed.clear();
  }

  collect(): string[] {
    return [...this.changed];
  }
}

const MAX_CACHE_BYTES = 128 * 1024 * 1024;

function patchMountableFs(mfs: MountableFs) {
  (mfs as any).readdirWithFileTypes = function (path: string) {
    const { fs, relativePath } = (this as any).routePath(path);
    return fs.readdirWithFileTypes(relativePath);
  };
}

function createCachedFs(inner: IFileSystem, watcher: FileWatcher) {
  const contentCache = new Map<string, string>();
  const dirCache = new Map<string, any[]>();
  const statCache = new Map<string, any>();
  let cacheBytes = 0;

  function evict() {
    for (const [key, val] of contentCache) {
      if (cacheBytes <= MAX_CACHE_BYTES) break;
      cacheBytes -= val.length;
      contentCache.delete(key);
      statCache.delete(key);
    }
  }

  watcher.on((path: string) => {
    const old = contentCache.get(path);
    if (old !== undefined) cacheBytes -= old.length;
    contentCache.delete(path);
    statCache.delete(path);
    const parent = path.substring(0, path.lastIndexOf('/')) || '/';
    dirCache.delete(parent);
  });

  const overrides: Record<string, any> = {
    async readFile(path: string, options?: any) {
      const cached = contentCache.get(path);
      if (cached !== undefined) {
        contentCache.delete(path);
        contentCache.set(path, cached);
        return cached;
      }
      const content = await inner.readFile(path, options);
      contentCache.set(path, content);
      cacheBytes += content.length;
      evict();
      return content;
    },
    async stat(path: string) {
      const cached = statCache.get(path);
      if (cached) return cached;
      const s = await inner.stat(path);
      statCache.set(path, s);
      return s;
    },
    async readdirWithFileTypes(path: string) {
      const cached = dirCache.get(path);
      if (cached) return cached;
      const entries = await inner.readdirWithFileTypes!(path);
      dirCache.set(path, entries);
      return entries;
    },

    async writeFile(path: string, content: any, options?: any) {
      await inner.writeFile(path, content, options);
      watcher.notify(path);
      if (typeof content === 'string') {
        contentCache.set(path, content);
        cacheBytes += content.length;
        evict();
      }
    },
    async appendFile(path: string, content: any, options?: any) {
      await inner.appendFile(path, content, options);
      watcher.notify(path);
    },
    async rm(path: string, options?: any) {
      await inner.rm(path, options);
      watcher.notify(path);
    },
    async mkdir(path: string, options?: any) {
      await inner.mkdir(path, options);
      watcher.notify(path);
    },
    async mv(src: string, dest: string) {
      await inner.mv(src, dest);
      watcher.notify(src);
      watcher.notify(dest);
    },
  };

  return new Proxy(inner, {
    get(target, prop) {
      if (prop in overrides) return overrides[prop as string];
      const val = (target as any)[prop];
      return typeof val === 'function' ? val.bind(target) : val;
    },
  }) as IFileSystem;
}

export function setupBash(
  mounts: Array<[string, string]>,
  env: Record<string, string>,
  watcher: FileWatcher,
  compileWasm: (hostPath: string) => Promise<WebAssembly.Module>,
): (cmd: string) => Promise<string> {
  const mfs = new MountableFs({
    base: new InMemoryFs(),
    mounts: mounts.map(([vfs, host]) => ({
      mountPoint: vfs,
      filesystem: new ReadWriteFs({ root: host }),
    })),
  });
  patchMountableFs(mfs);

  const cachedFs = createCachedFs(mfs, watcher);
  const bashInstance = new Bash({
    fs: cachedFs,
    cwd: '/workspace',
    env: { ...env, HOME: '/workspace', USER: 'user' },
    network: { dangerouslyAllowFullInternetAccess: true },
    executionLimits: {
      maxCommandCount: 50000,
      maxLoopIterations: 50000,
      maxCallDepth: 100,
      maxAwkIterations: 10000,
      maxSedIterations: 10000,
    },
  });

  const resolvePath = createResolvePath(mounts);
  const registered = new Map<string, string>();

  const refreshWasmCommands = () => {
    for (const [, host] of mounts) {
      const dir = `${host}/${WASM_BIN_DIR}`;
      let entries: Deno.DirEntry[];
      try {
        entries = [...Deno.readDirSync(dir)];
      } catch {
        continue;
      }
      for (const entry of entries) {
        if (!entry.isFile || !entry.name.endsWith('.wasm')) continue;
        const name = entry.name.replace(/\.wasm$/, '');
        const hostPath = `${dir}/${entry.name}`;
        let stamp: string;
        try {
          const st = Deno.statSync(hostPath);
          stamp = `${hostPath}:${st.mtime?.getTime() ?? 0}:${st.size}`;
        } catch {
          continue;
        }
        if (registered.get(name) === stamp) continue;
        registered.set(name, stamp);
        bashInstance.registerCommand(
          makeWasiBashCommand(
            name,
            hostPath,
            resolvePath,
            compileWasm,
          ) as Parameters<typeof bashInstance.registerCommand>[0],
        );
      }
    }
  };

  return async (cmd: string): Promise<string> => {
    try {
      refreshWasmCommands();
      const result = await bashInstance.exec(cmd);
      return JSON.stringify({
        stdout: result.stdout,
        stderr: result.stderr,
        exitCode: result.exitCode,
      });
    } catch (e) {
      return JSON.stringify({
        stdout: '',
        stderr: e instanceof Error ? e.message : String(e),
        exitCode: 1,
      });
    }
  };
}
