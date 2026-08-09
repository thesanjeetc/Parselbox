"""MCP Apps view rendering.

Agents emit HTML with display(); this turns it into a complete document with the
styling and callback runtime injected, ready for the client's renderer.
"""

import re

RESOURCE_DOMAINS = [
    "'unsafe-inline'",
    "https://cdn.jsdelivr.net",
    "https://unpkg.com",
    "https://esm.sh",
    "*",
    "data:",
    "blob:",
]

CONNECT_DOMAINS = [
    "'self'",
    "https://cdn.jsdelivr.net",
    "https://unpkg.com",
    "https://esm.sh",
]


def _origins(serve: int | None) -> list[str]:
    if not serve:
        return []
    return [f"http://localhost:{serve}", f"http://127.0.0.1:{serve}"]


def csp(serve: int | None = None) -> dict:
    """Content Security Policy for the app iframe.

    frameDomains is not optional: the renderer nests each view in an iframe, and
    without frame-src the browser blocks it and the widget shows nothing.
    Both connect- and resourceDomains name the sandbox's own web server: the view
    is hosted on the client's origin, not on it, so pbx.call() (connect-src) and
    <img>/<video>/<link> pointing at http://localhost:<serve> (resource-src) both
    need it spelled out. Referencing a served file by URL instead of inlining it
    as a data: URI is also how a big asset stays out of the tool result — the
    result carries a short URL, and the browser fetches the bytes straight from
    the sandbox, well under the host's per-message size limit.
    """
    return {
        "resourceDomains": [*RESOURCE_DOMAINS, *_origins(serve)],
        "frameDomains": ["'self'", "blob:", "data:"],
        "connectDomains": [*CONNECT_DOMAINS, *_origins(serve)],
    }


def _runtime(serve: int | None) -> str:
    """Tailwind + daisyUI so agent markup looks designed without a build step, and
    pbx.call() to reach @api handlers.

    The view runs inside the client's iframe, not on the sandbox's origin, so
    pbx.call() has to address the sandbox's web server absolutely — a relative
    fetch resolves against the client and 404s.
    """
    base = f"http://localhost:{serve}" if serve else ""
    return f"""\
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@5">
<script>
window.pbx = {{
  base: {base!r},
  async call(path, body, {{ method = "POST", headers }} = {{}}) {{
    if (!this.base) {{
      throw new Error("pbx.call() needs the sandbox web server — start Parselbox with serve=<port>");
    }}
    if (!path.startsWith("/")) path = "/api/" + path;
    const r = await fetch(this.base + path, {{
      method,
      headers: {{ "Content-Type": "application/json", ...(headers || {{}}) }},
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }});
    const text = await r.text();
    let data;
    try {{ data = JSON.parse(text); }} catch {{ data = text; }}
    if (!r.ok) throw new Error(typeof data === "string" ? data : (data?.error || `HTTP ${{r.status}}`));
    return data;
  }},
}};
</script>
"""


_HEAD = (
    '<meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="color-scheme" content="light dark">'
)


def render(html: str, serve: int | None = None) -> str:
    """Wrap a fragment into a full document and inject the view runtime."""
    runtime = _runtime(serve)

    if not re.search(r"<html[\s>]", html, re.I):
        html = f'<!DOCTYPE html>\n<html lang="en"><head>{_HEAD}</head><body>{html}</body></html>'
    elif not re.match(r"\s*<!DOCTYPE", html, re.I):
        html = f"<!DOCTYPE html>\n{html}"

    if "</head>" in html:
        return html.replace("</head>", f"{runtime}</head>", 1)
    if "<body" in html:
        return html.replace("<body", f"<head>{runtime}</head><body", 1)
    return f"<head>{runtime}</head>{html}"
