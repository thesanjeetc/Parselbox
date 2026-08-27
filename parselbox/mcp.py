import contextlib
import hashlib
import mimetypes
import warnings
from typing import Literal

from fastmcp import Context, FastMCP
from fastmcp.resources.resource import Resource

try:
    from fastmcp.apps import AppConfig
except ImportError:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastmcp.server.apps import AppConfig

from . import __version__
from .hooks import ElicitHook
from .logging import logger
from .view import csp as ui_csp

RENDERER_HTML = """\
<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>parselbox</title>
<style>
  html,body{margin:0;padding:0;overflow:hidden;background:transparent}
  iframe{border:0;width:100%;display:none}
</style>
</head>
<body>
<iframe id="view" sandbox="allow-scripts allow-same-origin allow-forms allow-popups"></iframe>
<script type="module">
import { App } from "https://cdn.jsdelivr.net/npm/@modelcontextprotocol/ext-apps@1.7.4/dist/src/app-with-deps.js";

const view = document.getElementById("view");
let current = null;

function collapse() {
  view.style.display = "none";
  view.style.height = "0px";
  document.body.style.height = "0px";
  current = null;
}

function fit() {
  const doc = view.contentDocument;
  if (!doc?.body) return;
  const h = doc.body.scrollHeight;
  if (h > 0) {
    view.style.height = h + "px";
    document.body.style.height = h + "px";
  }
}

function render(html) {
  if (html === current) return;
  current = html;
  view.style.display = "block";
  view.srcdoc = html;
}

view.addEventListener("load", () => {
  fit();
  const doc = view.contentDocument;
  if (doc) new ResizeObserver(fit).observe(doc.documentElement);
});

// Hosts do not reliably forward structuredContent to the app, but they always
// forward content — and the same payload is serialised into both, so read
// whichever arrives.
function viewOf(result) {
  if (result?.structuredContent?.view) return result.structuredContent.view;
  for (const block of result?.content ?? []) {
    if (block?.type !== "text" || !block.text) continue;
    try {
      const parsed = JSON.parse(block.text);
      if (parsed?.view) return parsed.view;
    } catch { /* not our payload */ }
  }
  return null;
}

const app = new App({ name: "parselbox-renderer", version: "1.0.0", autoResize: true });

app.ontoolresult = (result) => {
  if (result?.isError) {
    const text = (result.content ?? []).map(c => c.text ?? "").join("\\n");
    render(`<pre style="color:#c00;font:12px/1.5 ui-monospace,monospace;padding:1em;margin:0">${text}</pre>`);
    return;
  }
  const html = viewOf(result);
  if (html) render(html);
  else collapse();
};

app.connect().catch(() => collapse());
</script>
</body>
</html>
"""

RENDERER_URI = f"ui://parselbox/renderer-{hashlib.sha256(RENDERER_HTML.encode()).hexdigest()[:8]}.html"


class ParselboxMCP:
    def __init__(self, sandbox, max_tokens_estimate=10000, elicit=False, ui=True):
        self.sandbox = sandbox
        self.elicit_hook = None
        if elicit:
            self.enable_elicit()

        self.max_tokens_estimate = max_tokens_estimate

        self.mcp = FastMCP(name="Parselbox", version=__version__)
        self.resources = set()

        self.ui = None
        self.set_ui(ui)

    def set_ui(self, enabled: bool):
        """Declare (or withhold) the MCP Apps renderer on the execute_code tool.

        With it, display() views render inline in the chat. Without it, the tool
        carries no ui metadata, nothing is preloaded, and the agent is never told
        display() is available.
        """
        if self.ui == enabled:
            return
        first = self.ui is None
        self.ui = enabled
        self.sandbox.ui = enabled

        app = None
        if enabled:
            policy = ui_csp(self.sandbox.serve)
            app = AppConfig(resource_uri=RENDERER_URI, visibility=["model"], csp=policy)
            self.mcp.resource(
                RENDERER_URI, name="parselbox-renderer", app=AppConfig(csp=policy)
            )(lambda: RENDERER_HTML)

        if not first:
            self.mcp.local_provider.remove_tool("execute_code")
        self.mcp.tool(self.execute_code, app=app)

    def register_resources(self, files) -> bool:
        """Expose an execution's files as resources. True if any were new."""
        added = False
        for filename in files:
            uri = f"file://{filename}"
            if uri in self.resources:
                continue
            added = True
            mime_type, _ = mimetypes.guess_type(filename)
            if not mime_type:
                mime_type = "application/octet-stream"

            def make_fn(f):
                def read():
                    return self.sandbox.read_file(f)

                return read

            resource = Resource.from_function(
                fn=make_fn(filename),
                uri=uri,
                name=filename,
                mime_type=mime_type,
            )
            self.resources.add(uri)
            self.mcp.add_resource(resource)
        return added

    def enable_elicit(self):
        if not self.elicit_hook:
            self.elicit_hook = ElicitHook()
            self.sandbox.hooks.append(self.elicit_hook)

    async def execute_code(self, code: str, ctx: Context):
        """
        Execute Python code in stateful execution environment with access to external tools.
        Run sbx.help() to learn more about how to use this sandbox.
        """
        if self.elicit_hook:
            self.elicit_hook.set_context(ctx)

        result = await self.sandbox.execute_code(code)

        if not result.is_success:
            return {"error": result.error}

        output_str = str(result.output or "")
        token_estimate = len(output_str) // 4
        output = result.output
        note = None

        if token_estimate > self.max_tokens_estimate:
            output = self.sandbox.toolkit.preview(result.output)
            note = (
                "Execution output too big. Output truncated via sbx.preview(). "
                "Please reduce verbosity by returning only necessary data."
            )

        if result.output is None:
            note = "The execution yielded no output. Please note only the final expression is returned. print() output is not captured."

        response = {"result": output}
        if note:
            response["note"] = note

        if result.files:
            response["files"] = result.files
            if self.register_resources(result.files):
                with contextlib.suppress(Exception):
                    await ctx.session.send_resource_list_changed()

        if result.view and self.ui:
            response["view"] = result.view

        return response

    async def run(
        self,
        transport: Literal["stdio", "http"] = "stdio",
        host: str = "0.0.0.0",
        port: int = 9000,
    ):
        await self.sandbox.connect()
        if transport == "stdio":
            logger.info("MCP available via stdio")
            await self.mcp.run_async(show_banner=False)
        else:
            logger.info(f"MCP available at http://{host}:{port}/mcp")
            await self.mcp.run_async(
                transport="http",
                stateless_http=True,
                host=host,
                port=port,
                show_banner=False,
                uvicorn_config={"access_log": False},
            )
