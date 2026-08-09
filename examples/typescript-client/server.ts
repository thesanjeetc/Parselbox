/**
 * Single-script TypeScript example: custom MCP tools + parselbox.
 *
 * 1. Starts two MCP servers on one port (different paths = different namespaces)
 * 2. Starts parselbox with --mcp pointing to both
 * 3. Sandbox code calls TypeScript tools via math.* and strings.*
 *
 * Run: deno run --allow-all server.ts
 */

import http from "node:http";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { ElicitRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";

const PORT = 9100;

function createMath() {
  const server = new McpServer({ name: "math", version: "1.0.0" });
  server.registerTool(
    "fibonacci",
    { description: "Calculate nth fibonacci number", inputSchema: { n: z.number() } },
    async ({ n }) => {
      let a = 0, b = 1;
      for (let i = 0; i < n; i++) [a, b] = [b, a + b];
      return { content: [{ type: "text" as const, text: String(a) }] };
    },
  );
  server.registerTool(
    "add",
    { description: "Add two numbers", inputSchema: { a: z.number(), b: z.number() } },
    async ({ a, b }) => ({ content: [{ type: "text" as const, text: String(a + b) }] }),
  );
  return server;
}

function createStrings() {
  const server = new McpServer({ name: "strings", version: "1.0.0" });
  server.registerTool(
    "greet",
    { description: "Generate a greeting", inputSchema: { name: z.string(), formal: z.boolean().optional() } },
    async ({ name, formal }) => ({
      content: [{ type: "text" as const, text: formal ? `Good day, ${name}.` : `Hey ${name}!` }],
    }),
  );
  server.registerTool(
    "reverse",
    { description: "Reverse a string", inputSchema: { text: z.string() } },
    async ({ text }) => ({
      content: [{ type: "text" as const, text: [...text].reverse().join("") }],
    }),
  );
  return server;
}

const factories: Record<string, () => McpServer> = {
  "/math": createMath,
  "/strings": createStrings,
};

const httpServer = http.createServer(async (req, res) => {
  const factory = factories[req.url!];
  if (!factory) return res.writeHead(404).end();
  const server = factory();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined as any });
  await server.connect(transport);
  await transport.handleRequest(req, res);
});

await new Promise<void>((resolve) => httpServer.listen(PORT, resolve));

// --- Parselbox client ---

const clientTransport = new StdioClientTransport({
  command: "uv",
  args: [
    "run",
    "parselbox",
    "--mcp",
    JSON.stringify({
      mcpServers: {
        math: { type: "http", url: `http://localhost:${PORT}/math` },
        strings: { type: "http", url: `http://localhost:${PORT}/strings` },
      },
    }),
  ],
  cwd: "../..",
});

const client = new Client(
  { name: "ts-client", version: "1.0.0" },
  { capabilities: { elicitation: {} } },
);

client.setRequestHandler(ElicitRequestSchema, async (request) => {
  const event = JSON.parse(request.params.message);
  console.log(`[hook] ${event.hook}`, event.callback?.name || "");
  return { action: "accept" as const, content: { allow: true } };
});

async function run(code: string): Promise<void> {
  await client.callTool({
    name: "execute_code",
    arguments: { code },
  });
}

await client.connect(clientTransport);

await run("sbx.info()");
await run("sbx.search('fibonacci|greet|reverse')");
await run("sbx.inspect(['math.fibonacci', 'strings.greet'])");
await run("math.fibonacci(n=10)");
await run("math.add(a=100, b=200)");
await run("strings.greet(name='World')");
await run("strings.reverse(text='parselbox')");

await client.close();
httpServer.close();
