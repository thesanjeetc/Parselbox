import type { PyodideInterface } from 'pyodide';
import { createCrashPromise, setupPyodide } from './setup.ts';
import { FileWatcher, setupBash } from './filesystem.ts';
import { setWasiWriteNotifier } from './wasi.ts';
import { HttpServer } from './serve.ts';
import { logger } from './rpc.ts';
import { z } from 'zod';
import path from 'node:path';

const PACKAGE_DOWNLOAD_DOMAINS = [
  'cdn.jsdelivr.net:443',
  'pypi.org:443',
  'files.pythonhosted.org:443',
];

async function readPackagePermissions(cacheDir?: string) {
  const netPermissions = await Promise.all(
    PACKAGE_DOWNLOAD_DOMAINS.map((host) =>
      Deno.permissions.query({ name: 'net', host })
    ),
  );
  const writePerms = cacheDir
    ? await Deno.permissions.query({ name: 'write', path: cacheDir })
    : null;
  return {
    isNetworkDisabled: netPermissions.every(
      (perm: any) => perm.state !== 'granted',
    ),
    isRuntimePackagesDisabled: !writePerms || writePerms.state !== 'granted',
  };
}

const TIMEOUT_WORKER_JS = `
  let timer = null;
  self.onmessage = (e) => {
    if (timer) clearTimeout(timer);
    if (e.data.cancel) return;
    const buf = new Int32Array(e.data.buffer);
    timer = setTimeout(() => { Atomics.store(buf, 0, 2); timer = null; }, e.data.timeout);
  };
`;

const PY_CANCEL_TASKS = `
for _t in list(ParselboxTask._all.values()):
    _t.cancel()
ParselboxTask._all.clear()
`;

const ConfigureSchemaShape = {
  globals: z.record(z.any()).optional(),
  mounts: z.record(z.string()).optional(),
  files_dir: z.string().optional(),
  output_dir: z.string().optional(),
  tmp_dir: z.string().optional(),
  context: z.array(z.string()).optional(),
  packages: z.array(z.string()).optional(),
  disable_net: z.boolean().optional().default(true),
  allow_runtime_packages: z.boolean().optional().default(false),
  package_dir: z.string().optional(),
  memory: z.number().optional(),
  timeout: z.number().optional(),
  serve: z.number().min(1).max(65535).optional(),
};

const ConfigureSchema = z.object(ConfigureSchemaShape);
type SandboxConfig = z.infer<typeof ConfigureSchema>;

export class PyodideManager {
  public pyodide!: PyodideInterface;
  public workDir = '/workspace';
  private config: SandboxConfig;
  private httpServer: HttpServer | null = null;
  private interruptBuffer!: SharedArrayBuffer;
  private timeoutWorker: Worker | null = null;
  private timeout: number = 0;
  private npmImport!: (name: string, alias: string) => Promise<any>;
  private compileWasm!: (hostPath: string) => Promise<WebAssembly.Module>;
  private watcher!: FileWatcher;
  private mounts!: Array<[string, string]>;
  private fsWatcher: Deno.FsWatcher | null = null;

  resolveToHost(vfsPath: string): string | null {
    for (const [vfs, host] of this.mounts) {
      if (vfsPath === vfs || vfsPath.startsWith(vfs + '/')) {
        return host + vfsPath.slice(vfs.length);
      }
    }
    return null;
  }

  isUserFile(f: string): boolean {
    if (
      !((f.startsWith('/workspace/') || f.startsWith('/files/') ||
        f.startsWith('/mnt/')) &&
        !f.includes('/.parselbox/'))
    ) return false;
    const host = this.resolveToHost(f);
    if (!host) return false;
    try {
      return Deno.statSync(host).isFile;
    } catch {
      return false;
    }
  }

  constructor(rawConfig: Record<string, any> = {}) {
    this.config = ConfigureSchema.parse(rawConfig);
  }

  async start() {
    await this.initPyodide();
    await this.restorePackageCache();
    await this.applyConfig(this.config);
  }

  async restart() {
    logger.warn('deno', 'Restarting Pyodide after crash...');
    await this.initPyodide();
    await this.restorePackageCache();
    await this.applyConfig({ ...this.config, packages: [] });
  }

  private buildMounts(): Array<[string, string]> {
    const mounts: Array<[string, string]> = [];
    if (this.config.output_dir) {
      mounts.push(['/workspace', this.config.output_dir]);
    }
    if (this.config.tmp_dir) mounts.push(['/tmp', this.config.tmp_dir]);
    if (this.config.files_dir) mounts.push(['/files', this.config.files_dir]);
    if (this.config.mounts) {
      for (const [hostDir, name] of Object.entries(this.config.mounts)) {
        mounts.push([`/mnt/${name}`, hostDir as string]);
      }
    }
    return mounts.sort((a, b) => b[0].length - a[0].length);
  }

  private buildEnv(): Record<string, string> {
    const env: Record<string, string> = {
      MPLBACKEND: 'Agg',
      _: '',
      PYTHONEXECUTABLE: '',
    };
    const SKIP =
      /^(DENO_|NPM_|PARSELBOX_|MALLOC_)|^(HTTP_PROXY|HTTPS_PROXY|NO_PROXY|NO_COLOR|PYTHONEXECUTABLE|_)$/;
    for (const [k, v] of Object.entries(Deno.env.toObject())) {
      if (!SKIP.test(k)) env[k] = v;
    }
    return env;
  }

  private async initPyodide() {
    this.mounts = this.buildMounts();
    const env = this.buildEnv();

    const result = await setupPyodide(this.config, this.mounts, env);
    this.pyodide = result.pyodide;
    this.npmImport = result.npmImport;
    this.compileWasm = result.compileWasm;

    const NODEFS = this.pyodide.FS.filesystems.NODEFS;
    const FS = this.pyodide.FS;
    NODEFS.tryFSOperation = function (f: () => any) {
      try {
        return f();
      } catch (e: any) {
        if (e.name === 'NotCapable') {
          throw new FS.ErrnoError(NODEFS.convertNodeCode({ code: 'EACCES' }));
        }
        if (!e.code) {
          throw new FS.ErrnoError(NODEFS.convertNodeCode({ code: 'EIO' }));
        }
        if (e.code === 'UNKNOWN') throw new FS.ErrnoError(22);
        throw new FS.ErrnoError(NODEFS.convertNodeCode(e));
      }
    };

    this.interruptBuffer = new SharedArrayBuffer(4);
    this.pyodide.setInterruptBuffer(new Int32Array(this.interruptBuffer));

    this.watcher = new FileWatcher();
    setWasiWriteNotifier((p) => this.watcher.notify(p));

    for (
      const hook of [
        'onWriteToFile',
        'onDeletePath',
        'onMakeDirectory',
        'onRemoveDirectory',
        'onMakeSymlink',
      ]
    ) {
      this.pyodide.FS.trackingDelegate[hook] = (p: string) =>
        this.watcher.notify(p);
    }
    this.pyodide.FS.trackingDelegate['onMovePath'] = (a: string, b: string) => {
      this.watcher.notify(a);
      this.watcher.notify(b);
    };

    const bash = setupBash(this.mounts, env, this.watcher, this.compileWasm);
    this.pyodide.globals.set('_pbx_bash', bash);

    try {
      if (this.fsWatcher) {
        try {
          this.fsWatcher.close();
        } catch {}
      }
      this.fsWatcher = Deno.watchFs(this.mounts.map(([_, host]) => host));
      const reverseMounts = this.mounts
        .map(([vfs, host]) => [host, vfs] as [string, string])
        .sort((a, b) => b[0].length - a[0].length);
      const watcher = this.fsWatcher;
      (async () => {
        try {
          for await (const event of watcher) {
            if (event.kind === 'access') continue;
            for (const p of event.paths) {
              for (const [host, vfs] of reverseMounts) {
                if (p === host || p.startsWith(host + '/')) {
                  this.watcher.notify(vfs + p.slice(host.length));
                  break;
                }
              }
            }
          }
        } catch {}
      })();
    } catch {}

    if (this.config.timeout) this.timeout = this.config.timeout * 1000;

    if (!this.timeoutWorker) {
      const url = URL.createObjectURL(
        new Blob([TIMEOUT_WORKER_JS], { type: 'application/javascript' }),
      );
      this.timeoutWorker = new Worker(url, { type: 'module' });
    }
  }

  async findAndInstallPackages(target: string[] | string) {
    await this.pyodide.loadPackage(['micropip', 'ssl']);

    const pkgs = this.pyodide.globals.get('ParselboxPackages');
    let resultProxy;

    try {
      resultProxy = await pkgs.install(target);

      const result = resultProxy.toJs({
        dict_converter: Object.fromEntries,
      });

      return {
        installed: (result.installed as string[]) || [],
        failed: (result.failed as string[]) || [],
        error: result.error,
      };
    } finally {
      if (resultProxy) resultProxy.destroy();
      pkgs.destroy();
    }
  }

  private restorePackageCache() {
    const cacheDir = this.config.package_dir;
    if (!cacheDir) return;

    const siteDir = `${cacheDir}/site-packages`;
    const pkgs = this.pyodide.globals.get('ParselboxPackages');
    const spPath: string = pkgs.SITE_PACKAGES;
    pkgs.destroy();

    this.pyodide.FS.mount(
      this.pyodide.FS.filesystems.NODEFS,
      { root: siteDir },
      spPath,
    );
  }

  private async applyConfig(args: SandboxConfig) {
    if (args.globals) {
      for (const [key, value] of Object.entries(args.globals)) {
        this.pyodide.globals.set(key, this.pyodide.toPy(value));
      }
    }

    if (args.output_dir) {
      this.pyodide.FS.mount(
        this.pyodide.FS.filesystems.NODEFS,
        { root: args.output_dir },
        this.workDir,
      );
      this.pyodide.FS.mkdirTree('/workspace/.parselbox/tasks');
    }
    if (args.tmp_dir) {
      try {
        this.pyodide.FS.unmount('/tmp');
      } catch {
        /* ok */
      }
      this.pyodide.FS.mount(
        this.pyodide.FS.filesystems.NODEFS,
        { root: args.tmp_dir },
        '/tmp',
      );
    }
    if (args.files_dir) {
      const filesRoot = '/files';
      try {
        this.pyodide.FS.mkdirTree(filesRoot);
      } catch {}
      this.pyodide.FS.mount(
        this.pyodide.FS.filesystems.NODEFS,
        { root: args.files_dir },
        filesRoot,
      );
    }
    if (args.mounts) {
      const mountRoot = '/mnt';
      try {
        this.pyodide.FS.mkdirTree(mountRoot);
      } catch {}
      const sys = this.pyodide.pyimport('sys');
      if (!sys.path.includes(mountRoot)) sys.path.append(mountRoot);

      for (const [hostPath, name] of Object.entries(args.mounts)) {
        const mountPoint = path.join(mountRoot, name);
        try {
          this.pyodide.FS.mkdirTree(mountPoint);
        } catch {}
        this.pyodide.FS.mount(
          this.pyodide.FS.filesystems.NODEFS,
          { root: hostPath },
          mountPoint,
        );

        if (name.split('/').filter(Boolean).join('/') === 'skills') {
          if (!sys.path.includes(mountPoint)) sys.path.append(mountPoint);
        }
      }
      sys.destroy();
    }

    if (args.context?.length) {
      const DynamicProxyClass = this.pyodide.globals.get('ParselboxNamespace');
      const pyBuiltins = this.pyodide.globals.get('__builtins__');
      args.context.forEach((name) => {
        const proxy = DynamicProxyClass(name, null);
        this.pyodide.globals.set(name, proxy);
        pyBuiltins[name] = proxy;
      });
      DynamicProxyClass.destroy();
      pyBuiltins.destroy();
    }

    if (args.packages?.length) {
      const npmPkgs = args.packages.filter((p) => p.startsWith('npm:'));
      const pyPkgs = args.packages.filter((p) => !p.startsWith('npm:'));

      const npmInstalled: string[] = [];
      const npmFailed: string[] = [];
      for (const pkg of npmPkgs) {
        const name = pkg.replace('npm:', '');
        try {
          const alias = name.replace(/[-@/.]/g, '_').replace(/^_+/, '');
          await this.npmImport(name, alias);
          npmInstalled.push(name);
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          logger.warn('deno', `Failed to install ${name}: ${msg}`);
          npmFailed.push(name);
        }
      }
      if (npmInstalled.length > 0) {
        logger.info('deno', `Installed packages: ${npmInstalled.join(', ')}`);
      }
      if (npmFailed.length > 0) {
        logger.error(
          'deno',
          `Failed to install packages: ${npmFailed.join(', ')}`,
        );
      }

      if (pyPkgs.length) {
        const perms = await readPackagePermissions(this.config.package_dir);
        if (perms.isNetworkDisabled) {
          logger.error(
            'pyodide',
            'Network access is required to install Python packages.',
          );
        }

        const result = await this.findAndInstallPackages(pyPkgs);

        if (result.error) {
          logger.error(
            'pyodide',
            `Package installation error: ${result.error}`,
          );
        }
        if (result.failed.length > 0) {
          logger.error(
            'pyodide',
            `Failed to install packages: ${result.failed.join(', ')}`,
          );
        }
        if (result.installed.length > 0) {
          logger.info(
            'pyodide',
            `Installed packages: ${result.installed.join(', ')}`,
          );
        }
      }
    }

    if (args.serve) {
      if (this.httpServer) {
        this.httpServer.stop();
      }

      const indexHost = this.resolveToHost('/workspace/index.html');
      if (indexHost) {
        try {
          Deno.statSync(indexHost);
        } catch {
          Deno.writeTextFileSync(indexHost, 'hello from parselbox!');
        }
      }

      this.httpServer = new HttpServer(this.pyodide, {
        port: args.serve,
        liveReload: true,
      }, this);
      await this.httpServer.start();

      let reloadTimer: ReturnType<typeof setTimeout> | null = null;
      this.watcher.on((f) => {
        if (
          (f.startsWith('/workspace/') || f.startsWith('/files/')) &&
          /\.(html|css|js|png|jpg|svg|ico|gif|webp)$/i.test(f)
        ) {
          if (reloadTimer) clearTimeout(reloadTimer);
          reloadTimer = setTimeout(() => {
            this.httpServer?.notifyReload();
            reloadTimer = null;
          }, 50);
        }
      });
    }

    if (args.disable_net) await Deno.permissions.revoke({ name: 'net' });
    if (!args.allow_runtime_packages) {
      await Deno.permissions.revoke({
        name: 'write',
        path: this.config.package_dir,
      });
    }
  }

  async execute(code: string) {
    const pyodide = this.pyodide;

    try {
      if (this.config.allow_runtime_packages) {
        try {
          const { installed, failed } = await this.findAndInstallPackages(code);

          if (installed.length > 0) {
            logger.info('deno', `Auto-installed: ${installed.join(', ')}`);
          }
          if (failed.length > 0) {
            logger.warn(
              'deno',
              `Auto-install failed for: ${failed.join(', ')}`,
            );
          }
        } catch {}
      }

      this.watcher.clear();

      const execOptions = {
        filename: 'scratchpad.py',
        return_mode: 'last_expr' as const,
      };

      if (this.timeout > 0 && this.timeoutWorker) {
        Atomics.store(new Int32Array(this.interruptBuffer), 0, 0);
        this.timeoutWorker.postMessage({
          buffer: this.interruptBuffer,
          timeout: this.timeout,
        });
      }

      let execution: unknown;
      try {
        execution = await Promise.race([
          pyodide.runPythonAsync(code, execOptions),
          createCrashPromise(),
        ]);
      } finally {
        this.timeoutWorker?.postMessage({ cancel: true });
      }

      const rpc = pyodide.globals.get('ParselboxRpc');
      const jsonStr = rpc.serialize(execution);
      rpc.destroy();
      const result = JSON.parse(jsonStr);

      const files = this.watcher.collect().filter((f) => this.isUserFile(f));

      const capture = pyodide.globals.get('ParselboxCapture');
      const [stdout, stderr] = capture.collect().toJs();
      capture.destroy();

      return {
        is_success: true,
        output: result,
        files,
        stdout: stdout || null,
        stderr: stderr || null,
      };
    } catch (error) {
      let message = error instanceof Error ? error.message : String(error);
      message = message.replace(/^PythonError: +/, '');
      message = message.replace(
        / {2}File "\/lib\/python\d+\.zip\/_pyodide\/.*\n {4}.*\n(?: {4}.*\n)*/g,
        '',
      );
      const isTimeout = message.includes('KeyboardInterrupt') &&
        this.timeout > 0;
      if (isTimeout) {
        message = `Execution timed out after ${this.timeout / 1000}s.`;
        try {
          pyodide.runPython(PY_CANCEL_TASKS);
        } catch {}
      }

      const isSystemError = (message.includes('Requires write access') &&
        !message.includes('NotCapable')) ||
        message.includes('restarted') ||
        message.includes('fatally failed');

      let stdout: string | null = null;
      let stderr: string | null = null;
      try {
        const capture = pyodide.globals.get('ParselboxCapture');
        [stdout, stderr] = capture.collect().toJs();
        capture.destroy();
      } catch {}

      if (isSystemError) {
        logger.error('pyodide', `System error: ${message}`);
        await this.restart();
      } else {
        logger.debug('pyodide', `Execution error: ${message.split('\n')[0]}`);
      }

      return {
        is_success: false,
        error: message,
        stdout: stdout || null,
        stderr: stderr || null,
      };
    }
  }
}
