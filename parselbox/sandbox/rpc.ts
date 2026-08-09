import { TextLineStream } from '@std/streams/text-line-stream';

Object.freeze(JSON);
Object.freeze(Map.prototype);

type Handler = (params: any) => Promise<any> | any;
type PendingCall = { resolve: (v: any) => void; reject: (e: Error) => void };

export class RPC {
  #handlers: Record<string, Handler> = {};
  #pending = new Map<string, PendingCall>();
  #encoder = new TextEncoder();

  #send(msg: any) {
    Deno.stdout.writeSync(this.#encoder.encode(JSON.stringify(msg) + '\n'));
  }

  notify(method: string, params: any) {
    this.#send({ method, params });
  }

  #log(level: string, category: string, message: string) {
    this.notify('log', { level, category, message });
  }

  logger = {
    debug: (cat: string, msg: string) => this.#log('debug', cat, msg),
    info: (cat: string, msg: string) => this.#log('info', cat, msg),
    warn: (cat: string, msg: string) => this.#log('warning', cat, msg),
    error: (cat: string, msg: string) => this.#log('error', cat, msg),
  };

  call(method: string, params: any): Promise<any> {
    const id = crypto.randomUUID();
    const promise = new Promise<any>((resolve, reject) =>
      this.#pending.set(id, { resolve, reject })
    );
    this.#send({ id, method, params });
    return promise;
  }

  register(method: string, fn: Handler) {
    this.#handlers[method] = fn;
  }

  async #handleRequest(msg: any) {
    const handler = this.#handlers[msg.method];
    if (!handler) {
      if (msg.id !== undefined) {
        this.#send({ id: msg.id, error: `Method not found: ${msg.method}` });
      }
      return;
    }
    try {
      const result = await handler(msg.params);
      if (msg.id !== undefined) this.#send({ id: msg.id, result });
    } catch (e) {
      const error = e instanceof Error ? e.message : String(e);
      if (msg.id !== undefined) this.#send({ id: msg.id, error });
      else this.logger.error('rpc', `Handler error: ${error}`);
    }
  }

  async listen() {
    Object.freeze(this.#handlers);
    const lines = Deno.stdin.readable
      .pipeThrough(new TextDecoderStream())
      .pipeThrough(new TextLineStream());
    for await (const line of lines) {
      if (!line.trim()) continue;
      let msg: any;
      try {
        msg = JSON.parse(line);
      } catch {
        continue;
      }

      if ('result' in msg || 'error' in msg) {
        const p = this.#pending.get(msg.id);
        if (p) {
          this.#pending.delete(msg.id);
          'error' in msg
            ? p.reject(new Error(msg.error))
            : p.resolve(msg.result);
        }
      } else if ('method' in msg) {
        this.#handleRequest(msg);
      }
    }
  }
}

export const rpc = new RPC();
export const logger = rpc.logger;
