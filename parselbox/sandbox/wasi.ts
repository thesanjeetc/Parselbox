const ERRNO = {
  OK: 0,
  ACCES: 2,
  BADF: 8,
  EXIST: 20,
  INVAL: 28,
  IO: 29,
  ISDIR: 31,
  NOENT: 44,
  NOSYS: 52,
  NOTDIR: 54,
  SPIPE: 70,
  NOTCAP: 76,
};

const WASI_NS = 'wasi_snapshot_preview1';
const WASI_NS_OLD = 'wasi_unstable';

let notifyWrite: (vfsPath: string) => void = () => {};

export function setWasiWriteNotifier(fn: (vfsPath: string) => void) {
  notifyWrite = fn;
}

export function createResolvePath(mounts: Array<[string, string]>) {
  return (vfsPath: string): string => {
    if (!vfsPath.startsWith('/')) vfsPath = '/workspace/' + vfsPath;
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
    throw new Error(`"${vfsPath}" is not backed by a mounted directory`);
  };
}

export function makeWasiBashCommand(
  name: string,
  hostPath: string,
  resolvePath: (vfsPath: string) => string,
  compileWasm: (hostPath: string) => Promise<WebAssembly.Module>,
) {
  let runner: ReturnType<typeof makeWasiRunner> | null = null;
  return {
    name,
    async execute(
      args: string[],
      ctx: { stdin: string; env: Map<string, string> },
    ) {
      try {
        if (!runner) {
          const module = await compileWasm(hostPath);
          const info = analyzeWasm(module);
          if (!info.wasi || !info.hasStart) {
            return {
              stdout: '',
              stderr: `${name}: not a runnable WASI command\n`,
              exitCode: 126,
            };
          }
          runner = makeWasiRunner(module, name, resolvePath, info.wasiNs);
        }
        const env: Record<string, string> = {};
        for (const [k, v] of ctx.env) env[k] = v;
        const r = runner(args, { stdin: ctx.stdin, env });
        let stdout: string;
        let binary = false;
        try {
          stdout = new TextDecoder('utf-8', { fatal: true }).decode(r.stdout);
        } catch {
          stdout = new TextDecoder('latin1').decode(r.stdout);
          binary = true;
        }
        return {
          stdout,
          stderr: r.stderr,
          exitCode: r.exit,
          ...(binary ? { stdoutEncoding: 'binary' as const } : {}),
        };
      } catch (e) {
        return {
          stdout: '',
          stderr: `${name}: ${e instanceof Error ? e.message : String(e)}\n`,
          exitCode: 126,
        };
      }
    },
  };
}

export function analyzeWasm(module: WebAssembly.Module) {
  const imports = WebAssembly.Module.imports(module);
  const namespaces = [...new Set(imports.map((i) => i.module))];
  const exports = WebAssembly.Module.exports(module).map((e) => e.name);
  const wasiNs = namespaces.find((n) => n === WASI_NS || n === WASI_NS_OLD);
  return {
    selfContained: imports.length === 0,
    wasi: !!wasiNs,
    wasiNs: wasiNs ?? WASI_NS,
    otherNamespaces: namespaces.filter((n) => n !== wasiNs),
    hasStart: exports.includes('_start'),
  };
}

function toErrno(e: unknown): number {
  if (e instanceof Deno.errors.NotFound) return ERRNO.NOENT;
  if (e instanceof Deno.errors.AlreadyExists) return ERRNO.EXIST;
  if (e instanceof Deno.errors.NotCapable) return ERRNO.NOTCAP;
  if (e instanceof Deno.errors.PermissionDenied) return ERRNO.ACCES;
  if (e instanceof Deno.errors.IsADirectory) return ERRNO.ISDIR;
  if (e instanceof Deno.errors.NotADirectory) return ERRNO.NOTDIR;
  return ERRNO.IO;
}

interface OpenFd {
  file: Deno.FsFile | null;
  path: string;
  vfsPath: string;
  isDir: boolean;
  dirty: boolean;
}

interface RunOpts {
  stdin?: string | Uint8Array;
  env?: Record<string, string>;
  preopens?: Record<string, string>;
  argv0?: string;
}

export interface WasiResult {
  exit: number;
  stdout: Uint8Array;
  stderr: string;
  missing: string[];
}

const FD_WRITE_RIGHT = 1n << 6n;

export function makeWasiRunner(
  module: WebAssembly.Module,
  name: string,
  resolvePath: (vfsPath: string) => string,
  wasiNs: string = WASI_NS,
) {
  const legacy = wasiNs === WASI_NS_OLD;
  const run = (args?: string[], opts?: RunOpts): WasiResult => {
    const o = opts ?? {};
    const enc = new TextEncoder(),
      dec = new TextDecoder();
    const argv = [o.argv0 ?? name, ...(args ?? [])];
    const env = Object.entries({ HOME: '/', LANG: 'C.UTF-8', ...o.env }).map(
      ([k, v]) => `${k}=${v}`,
    );
    const stdinBytes = typeof o.stdin === 'string'
      ? enc.encode(o.stdin)
      : o.stdin
      ? new Uint8Array(o.stdin)
      : new Uint8Array(0);
    const stdin = { bytes: stdinBytes, pos: 0 };
    const outChunks: Uint8Array[] = [];
    let stderrText = '';
    const missing = new Set<string>();
    let memory: WebAssembly.Memory;
    const mem = () => new DataView(memory.buffer);

    const preopens: Array<{ guest: string; root: string | null }> = [
      { guest: '/', root: null },
      { guest: '.', root: null },
      ...Object.entries(o.preopens ?? {}).map(([guest, root]) => ({
        guest: '/' + guest.replace(/^\/+/, ''),
        root,
      })),
    ];
    const preopenFds = new Map(preopens.map((p, i) => [3 + i, p]));
    const fds = new Map<number, OpenFd>();
    let nextFd = 3 + preopens.length;

    function hostFor(
      dirfd: number,
      guestRel: string,
    ): { host: string; vfs: string } | null {
      const pre = preopenFds.get(dirfd);
      if (!pre) return null;
      const rel = guestRel.replace(/^\.?\/+/, '');
      for (
        const vfs of pre.root === null
          ? ['/' + rel, '/workspace/' + rel]
          : [`${pre.root}/${rel}`]
      ) {
        try {
          return { host: resolvePath(vfs), vfs };
        } catch {
        }
      }
      return null;
    }
    function readIovs(
      iovsPtr: number,
      iovsLen: number,
      fill: (base: number, len: number) => number,
    ) {
      const v = mem();
      let total = 0;
      for (let i = 0; i < iovsLen; i++) {
        const base = v.getUint32(iovsPtr + i * 8, true);
        const len = v.getUint32(iovsPtr + i * 8 + 4, true);
        total += fill(base, len);
      }
      return total;
    }
    function writeStrs(list: string[], ptrsP: number, bufP: number) {
      const v = mem();
      let p = bufP;
      list.forEach((s, i) => {
        v.setUint32(ptrsP + i * 4, p, true);
        const b = enc.encode(s);
        new Uint8Array(memory.buffer, p, b.length).set(b);
        v.setUint8(p + b.length, 0);
        p += b.length + 1;
      });
    }
    const strsLen = (l: string[]) =>
      l.reduce((s, e) => s + enc.encode(e).length + 1, 0);
    function fillStat(bufP: number, st: Partial<Deno.FileInfo>) {
      const sizeOff = legacy ? 24 : 32;
      new Uint8Array(memory.buffer, bufP, legacy ? 56 : 64).fill(0);
      const v = mem();
      v.setBigUint64(bufP, BigInt(st.dev ?? 1), true);
      v.setBigUint64(bufP + 8, BigInt(st.ino ?? 0), true);
      v.setUint8(bufP + 16, st.isDirectory ? 3 : st.isSymlink ? 7 : 4);
      if (legacy) v.setUint32(bufP + 20, 1, true);
      else v.setBigUint64(bufP + 24, 1n, true);
      v.setBigUint64(bufP + sizeOff, BigInt(st.size ?? 0), true);
      const ns = (d: Date | null | undefined) =>
        d ? BigInt(d.getTime()) * 1000000n : 0n;
      v.setBigUint64(bufP + sizeOff + 8, ns(st.atime), true);
      v.setBigUint64(bufP + sizeOff + 16, ns(st.mtime), true);
      v.setBigUint64(bufP + sizeOff + 24, ns(st.mtime), true);
    }
    function guestPath(pathP: number, pathLen: number): string {
      return dec.decode(new Uint8Array(memory.buffer, pathP, pathLen));
    }

    const known: Record<string, (...a: any[]) => number> = {
      args_sizes_get: (cP, bP) => {
        const v = mem();
        v.setUint32(cP, argv.length, true);
        v.setUint32(bP, strsLen(argv), true);
        return 0;
      },
      args_get: (aP, bP) => {
        writeStrs(argv, aP, bP);
        return 0;
      },
      environ_sizes_get: (cP, bP) => {
        const v = mem();
        v.setUint32(cP, env.length, true);
        v.setUint32(bP, strsLen(env), true);
        return 0;
      },
      environ_get: (eP, bP) => {
        writeStrs(env, eP, bP);
        return 0;
      },
      clock_time_get: (id, _p, rP) => {
        const ns = id === 0
          ? BigInt(Date.now()) * 1000000n
          : BigInt(Math.round(performance.now() * 1e6));
        mem().setBigUint64(rP, ns, true);
        return 0;
      },
      clock_res_get: (_i, rP) => {
        mem().setBigUint64(rP, 1000n, true);
        return 0;
      },
      random_get: (bP, len) => {
        for (let o = 0; o < len; o += 65536) {
          crypto.getRandomValues(
            new Uint8Array(memory.buffer, bP + o, Math.min(65536, len - o)),
          );
        }
        return 0;
      },
      fd_write: (fd, iovs, n, nwP) => {
        const f = fd > 2 ? fds.get(fd) : null;
        if (fd > 2 && (!f || !f.file)) return ERRNO.BADF;
        try {
          const w = readIovs(iovs, n, (base, len) => {
            const d = new Uint8Array(memory.buffer, base, len);
            if (fd === 2) stderrText += dec.decode(d);
            else if (fd <= 1) outChunks.push(new Uint8Array(d));
            else {
              let written = 0;
              while (written < d.length) {
                written += f!.file!.writeSync(d.subarray(written));
              }
              f!.dirty = true;
            }
            return len;
          });
          mem().setUint32(nwP, w, true);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_read: (fd, iovs, n, nrP) => {
        if (fd === 0) {
          const t = readIovs(iovs, n, (base, len) => {
            const c = stdin.bytes.subarray(stdin.pos, stdin.pos + len);
            new Uint8Array(memory.buffer, base, c.length).set(c);
            stdin.pos += c.length;
            return c.length;
          });
          mem().setUint32(nrP, t, true);
          return 0;
        }
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          let done = false;
          const t = readIovs(iovs, n, (base, len) => {
            if (done || len === 0) return 0;
            const got = f.file!.readSync(
              new Uint8Array(memory.buffer, base, len),
            );
            if (got === null || got === 0) {
              done = true;
              return 0;
            }
            if (got < len) done = true;
            return got;
          });
          mem().setUint32(nrP, t, true);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_seek: (fd, off, whence, newP) => {
        if (fd <= 2) return ERRNO.SPIPE;
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          const modes = legacy
            ? [Deno.SeekMode.Current, Deno.SeekMode.End, Deno.SeekMode.Start]
            : [Deno.SeekMode.Start, Deno.SeekMode.Current, Deno.SeekMode.End];
          const pos = f.file.seekSync(
            Number(off),
            modes[whence] ?? Deno.SeekMode.Start,
          );
          mem().setBigUint64(newP, BigInt(pos), true);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_tell: (fd, p) => {
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        mem().setBigUint64(
          p,
          BigInt(f.file.seekSync(0, Deno.SeekMode.Current)),
          true,
        );
        return 0;
      },
      fd_close: (fd) => {
        const f = fds.get(fd);
        f?.file?.close();
        if (f?.dirty) notifyWrite(f.vfsPath);
        fds.delete(fd);
        return 0;
      },
      fd_fdstat_get: (fd, p) => {
        const v = mem();
        const f = fds.get(fd);
        v.setUint8(p, fd <= 2 ? 2 : preopenFds.has(fd) || f?.isDir ? 3 : 4);
        v.setUint16(p + 2, 0, true);
        v.setBigUint64(p + 8, 0xffffffffffffffffn, true);
        v.setBigUint64(p + 16, 0xffffffffffffffffn, true);
        return 0;
      },
      fd_fdstat_set_flags: () => 0,
      fd_prestat_get: (fd, p) => {
        const pre = preopenFds.get(fd);
        if (!pre) return ERRNO.BADF;
        const v = mem();
        v.setUint8(p, 0);
        v.setUint32(p + 4, enc.encode(pre.guest).length, true);
        return 0;
      },
      fd_prestat_dir_name: (fd, p, len) => {
        const pre = preopenFds.get(fd);
        if (!pre) return ERRNO.BADF;
        const b = enc.encode(pre.guest);
        new Uint8Array(memory.buffer, p, Math.min(b.length, len)).set(
          b.subarray(0, len),
        );
        return 0;
      },
      path_open: (
        dirfd,
        _dirflags,
        pathP,
        pathLen,
        oflags,
        rightsBase,
        _rightsInheriting,
        fdflags,
        openedP,
      ) => {
        const loc = hostFor(dirfd, guestPath(pathP, pathLen));
        if (!loc) return ERRNO.NOENT;
        const hp = loc.host;
        const create = !!(oflags & 1);
        const wantsDir = !!(oflags & 2);
        const createNew = !!(oflags & 4);
        const truncate = !!(oflags & 8);
        const append = !!(fdflags & 1);
        const write = create ||
          truncate ||
          append ||
          !!(BigInt(rightsBase) & FD_WRITE_RIGHT);
        try {
          const st = create ? null : Deno.statSync(hp);
          if (st?.isDirectory || wantsDir) {
            if (st && !st.isDirectory) return ERRNO.NOTDIR;
            const fd = nextFd++;
            fds.set(fd, {
              file: null,
              path: hp,
              vfsPath: loc.vfs,
              isDir: true,
              dirty: false,
            });
            mem().setUint32(openedP, fd, true);
            return 0;
          }
          const file = Deno.openSync(hp, {
            read: true,
            write,
            create,
            createNew,
            truncate,
            append,
          });
          const fd = nextFd++;
          fds.set(fd, {
            file,
            path: hp,
            vfsPath: loc.vfs,
            isDir: false,
            dirty: create || truncate,
          });
          mem().setUint32(openedP, fd, true);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      path_filestat_get: (dirfd, _f, pathP, pathLen, bufP) => {
        const guest = guestPath(pathP, pathLen);
        if (guest.replace(/\/+/g, '') === '') {
          fillStat(bufP, { isDirectory: true });
          return 0;
        }
        const loc = hostFor(dirfd, guest);
        if (!loc) return ERRNO.NOENT;
        try {
          fillStat(bufP, Deno.statSync(loc.host));
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_filestat_get: (fd, bufP) => {
        if (fd <= 2) {
          fillStat(bufP, {});
          return 0;
        }
        const pre = preopenFds.get(fd);
        const f = fds.get(fd);
        if (!pre && !f) return ERRNO.BADF;
        try {
          if (f?.file) fillStat(bufP, f.file.statSync());
          else fillStat(bufP, { isDirectory: true });
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_filestat_set_size: (fd, size) => {
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          f.file.truncateSync(Number(size));
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_readdir: (fd, bufP, bufLen, cookie, retP) => {
        const f = fds.get(fd);
        const pre = preopenFds.get(fd);
        const dirHost = f?.isDir
          ? f.path
          : pre
          ? (hostFor(fd, '')?.host ?? null)
          : null;
        if (!dirHost) return ERRNO.NOTDIR;
        try {
          const entries = [...Deno.readDirSync(dirHost)].sort((a, b) =>
            a.name.localeCompare(b.name)
          );
          const v = mem();
          let used = 0;
          for (let i = Number(cookie); i < entries.length; i++) {
            const nameB = enc.encode(entries[i].name);
            const need = 24 + nameB.length;
            if (used + need > bufLen) {
              used = bufLen;
              break;
            }
            const p = bufP + used;
            v.setBigUint64(p, BigInt(i + 1), true);
            v.setBigUint64(p + 8, 0n, true);
            v.setUint32(p + 16, nameB.length, true);
            v.setUint8(
              p + 20,
              entries[i].isDirectory ? 3 : entries[i].isSymlink ? 7 : 4,
            );
            new Uint8Array(memory.buffer, p + 24, nameB.length).set(nameB);
            used += need;
          }
          v.setUint32(retP, used, true);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      path_create_directory: (dirfd, pathP, pathLen) => {
        const loc = hostFor(dirfd, guestPath(pathP, pathLen));
        if (!loc) return ERRNO.NOENT;
        try {
          Deno.mkdirSync(loc.host);
          notifyWrite(loc.vfs);
          return 0;
        } catch (e) {
          return e instanceof Deno.errors.AlreadyExists ? 0 : toErrno(e);
        }
      },
      path_unlink_file: (dirfd, pathP, pathLen) => {
        const loc = hostFor(dirfd, guestPath(pathP, pathLen));
        if (!loc) return ERRNO.NOENT;
        try {
          Deno.removeSync(loc.host);
          notifyWrite(loc.vfs);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      path_remove_directory: (dirfd, pathP, pathLen) => {
        const loc = hostFor(dirfd, guestPath(pathP, pathLen));
        if (!loc) return ERRNO.NOENT;
        try {
          Deno.removeSync(loc.host);
          notifyWrite(loc.vfs);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      path_rename: (dirfd, oldP, oldLen, newDirfd, newP, newLen) => {
        const from = hostFor(dirfd, guestPath(oldP, oldLen));
        const to = hostFor(newDirfd, guestPath(newP, newLen));
        if (!from || !to) return ERRNO.NOENT;
        try {
          Deno.renameSync(from.host, to.host);
          notifyWrite(from.vfs);
          notifyWrite(to.vfs);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_pread: (fd, iovs, n, off, nrP) => {
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          const saved = f.file.seekSync(0, Deno.SeekMode.Current);
          f.file.seekSync(Number(off), Deno.SeekMode.Start);
          const r = known.fd_read(fd, iovs, n, nrP);
          f.file.seekSync(saved, Deno.SeekMode.Start);
          return r;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_pwrite: (fd, iovs, n, off, nwP) => {
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          const saved = f.file.seekSync(0, Deno.SeekMode.Current);
          f.file.seekSync(Number(off), Deno.SeekMode.Start);
          const r = known.fd_write(fd, iovs, n, nwP);
          f.file.seekSync(saved, Deno.SeekMode.Start);
          return r;
        } catch (e) {
          return toErrno(e);
        }
      },
      fd_renumber: (from, to) => {
        const f = fds.get(from);
        if (!f) return ERRNO.BADF;
        fds.get(to)?.file?.close();
        fds.set(to, f);
        fds.delete(from);
        return 0;
      },
      fd_allocate: (fd, off, len) => {
        const f = fds.get(fd);
        if (!f || !f.file) return ERRNO.BADF;
        try {
          const size = f.file.statSync().size;
          const want = Number(off) + Number(len);
          if (want > size) f.file.truncateSync(want);
          return 0;
        } catch (e) {
          return toErrno(e);
        }
      },
      path_filestat_set_times: () => 0,
      path_readlink: () => ERRNO.INVAL,
      poll_oneoff: (iP, oP, n, nP) => {
        const v = mem();
        for (let i = 0; i < n; i++) {
          v.setBigUint64(oP + i * 32, v.getBigUint64(iP + i * 48, true), true);
          v.setUint16(oP + i * 32 + 8, 0, true);
          v.setUint8(oP + i * 32 + 10, v.getUint8(iP + i * 48 + 8));
        }
        v.setUint32(nP, n, true);
        return 0;
      },
      proc_exit: (code) => {
        throw new Error('__pbx_wasi_exit__:' + code);
      },
      sched_yield: () => 0,
      fd_sync: () => 0,
      fd_datasync: () => 0,
      fd_advise: () => 0,
    };
    const wasi = new Proxy(known, {
      get(t, prop: string) {
        if (prop in t) return t[prop];
        return () => {
          missing.add(prop);
          return ERRNO.NOSYS;
        };
      },
    });

    const instance = new WebAssembly.Instance(module, { [wasiNs]: wasi });
    memory = instance.exports.memory as WebAssembly.Memory;
    let exit = 0;
    try {
      (instance.exports._start as () => void)();
    } catch (e) {
      const m = String(e instanceof Error ? e.message : e).match(
        /__pbx_wasi_exit__:(\d+)/,
      );
      if (!m) throw e;
      exit = Number(m[1]);
    } finally {
      for (const f of fds.values()) {
        f.file?.close();
        if (f.dirty) notifyWrite(f.vfsPath);
      }
    }
    const total = outChunks.reduce((s, c) => s + c.length, 0);
    const out = new Uint8Array(total);
    let p = 0;
    for (const c of outChunks) {
      out.set(c, p);
      p += c.length;
    }
    return { exit, stdout: out, stderr: stderrText, missing: [...missing] };
  };
  (run as unknown as { _pbx_wasi: boolean })._pbx_wasi = true;
  return run;
}
