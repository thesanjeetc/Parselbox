import { contentType } from '@std/media-types';
import path from 'node:path';
import type { PyodideInterface } from 'pyodide';
import { logger } from './rpc.ts';

export type PathResolver = (vfsPath: string) => string;

export interface HttpServerConfig {
  port: number;
  host?: string;
  liveReload?: boolean;
}

const LIVE_RELOAD_SCRIPT =
  `<script>new EventSource('/_live').onmessage=()=>location.reload()</script>`;

export class HttpServer {
  private controller: AbortController | null = null;
  private sseClients: Set<ReadableStreamDefaultController> = new Set();

  constructor(
    private pyodide: PyodideInterface,
    private config: HttpServerConfig,
    private manager?: { resolveToHost: (p: string) => string | null },
  ) {}

  private getApi() {
    return this.pyodide.globals.get('ParselboxRouter');
  }

  notifyReload() {
    const data = new TextEncoder().encode('data: reload\n\n');
    for (const client of this.sseClients) {
      try {
        client.enqueue(data);
      } catch {
        this.sseClients.delete(client);
      }
    }
  }

  start(): Promise<void> {
    if (this.controller) {
      logger.warn('deno', 'Server already running');
      return Promise.resolve();
    }

    this.controller = new AbortController();
    const { port, host = '0.0.0.0' } = this.config;

    return new Promise<void>((resolve) => {
      Deno.serve(
        {
          port,
          hostname: host,
          signal: this.controller!.signal,
          onListen: () => {
            logger.info('deno', `Listening on http://${host}:${port}`);
            resolve();
          },
        },
        (req) => this.handleRequest(req),
      );
    });
  }

  stop() {
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
      this.sseClients.clear();
      logger.info('http', 'Server stopped');
    }
  }

  private async handleRequest(req: Request): Promise<Response> {
    const url = new URL(req.url);
    const { pathname } = url;

    logger.debug('http', `${req.method} ${pathname}`);

    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (req.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      if (pathname === '/_live') {
        return this.handleLiveReload(corsHeaders);
      }

      if (pathname === '/_upload' && req.method === 'POST') {
        return await this.handleUpload(req, corsHeaders);
      }

      if (pathname === '/_routes') {
        const api = this.pyodide.globals.get('ParselboxRouter');
        const resultProxy = api.list_routes();
        const result = resultProxy.toJs({ dict_converter: Object.fromEntries });
        resultProxy.destroy();
        api.destroy();
        return Response.json(result, { headers: corsHeaders });
      }

      if (pathname.startsWith('/api/')) {
        return await this.handleApiRequest(req, url, corsHeaders);
      }

      return this.serveStaticFile(pathname, corsHeaders);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      logger.error('http', `Request error: ${message}`);
      return Response.json(
        { error: message },
        { status: 500, headers: corsHeaders },
      );
    }
  }

  private async handleApiRequest(
    req: Request,
    url: URL,
    corsHeaders: Record<string, string>,
  ): Promise<Response> {
    const params = Object.fromEntries(url.searchParams);
    let body = null;

    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(req.method)) {
      const text = await req.text();
      if (text) {
        try {
          body = JSON.parse(text);
        } catch {
          return Response.json(
            { error: 'Invalid JSON body' },
            { status: 400, headers: corsHeaders },
          );
        }
      }
    }

    const api = this.getApi();
    let resultProxy;
    try {
      const handleFn = api.handle;
      resultProxy = await handleFn.callPromising(
        req.method,
        url.pathname,
        params,
        body,
      );
      api.destroy();
    } catch (err) {
      api.destroy();
      const msg = err instanceof Error ? err.message : String(err);
      logger.error('http', `Handler call failed: ${msg}`);
      return Response.json(
        { error: msg },
        { status: 500, headers: corsHeaders },
      );
    }

    const result = resultProxy.toJs({ dict_converter: Object.fromEntries });
    resultProxy.destroy();

    if (result.status >= 400) {
      logger.error('http', `Handler error: ${JSON.stringify(result.body)}`);
    }

    return Response.json(result.body, {
      status: result.status,
      headers: corsHeaders,
    });
  }

  private async handleUpload(
    req: Request,
    corsHeaders: Record<string, string>,
  ): Promise<Response> {
    const formData = await req.formData();
    const uploaded: { name: string; path: string; size: number }[] = [];

    for (const [key, value] of formData.entries()) {
      if (value instanceof File) {
        let filename = value.name || key;
        filename = path.basename(filename).replace(/^\.+/, '');
        if (!filename) {
          filename = `upload_${Date.now()}`;
        }
        const filePath = `/files/${filename}`;
        const data = new Uint8Array(await value.arrayBuffer());

        const hostUploadPath = this.manager!.resolveToHost(filePath);
        if (hostUploadPath) {
          Deno.writeFileSync(hostUploadPath, data);
        }

        uploaded.push({
          name: filename,
          path: filePath,
          size: data.length,
        });

        logger.info('http', `Uploaded: ${filePath} (${data.length} bytes)`);
      }
    }

    if (uploaded.length === 0) {
      return Response.json(
        { error: 'No files in request' },
        { status: 400, headers: corsHeaders },
      );
    }

    return Response.json({ uploaded }, { headers: corsHeaders });
  }

  private handleLiveReload(corsHeaders: Record<string, string>): Response {
    let streamController: ReadableStreamDefaultController | null = null;
    const stream = new ReadableStream({
      start: (controller) => {
        streamController = controller;
        this.sseClients.add(controller);
      },
      cancel: () => {
        if (streamController) {
          this.sseClients.delete(streamController);
        }
      },
    });

    return new Response(stream, {
      headers: {
        ...corsHeaders,
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      },
    });
  }

  private serveStaticFile(
    pathname: string,
    corsHeaders: Record<string, string>,
  ): Response {
    let fullPath: string;

    if (pathname.startsWith('/files/')) {
      const safeName = path.basename(decodeURIComponent(pathname));
      fullPath = `/files/${safeName}`;
    } else {
      let safePath = path.normalize(pathname).replace(/^\/+/, '');
      if (safePath === '' || safePath === '.') {
        safePath = 'index.html';
      }
      fullPath = path.join('/workspace', safePath);
      if (!fullPath.startsWith('/workspace')) {
        return Response.json(
          { error: 'Invalid path' },
          { status: 400, headers: corsHeaders },
        );
      }
    }

    try {
      const hostPath = this.manager!.resolveToHost(fullPath);
      if (!hostPath) throw new Error('Not found');
      const data = Deno.readFileSync(hostPath);

      const ext = path.extname(fullPath);
      const mime = (ext === '.ts' || ext === '.tsx')
        ? 'text/plain'
        : contentType(ext) || 'application/octet-stream';

      if (this.config.liveReload && mime.includes('text/html')) {
        const html = new TextDecoder().decode(data) + LIVE_RELOAD_SCRIPT;
        return new Response(html, {
          headers: { ...corsHeaders, 'Content-Type': mime },
        });
      }

      return new Response(new Uint8Array(data), {
        headers: { ...corsHeaders, 'Content-Type': mime },
      });
    } catch {
      return Response.json(
        { error: 'File not found' },
        { status: 404, headers: corsHeaders },
      );
    }
  }
}
