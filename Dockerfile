FROM python:3.13-slim

COPY --from=denoland/deno:bin-2.6.5 /deno /usr/local/bin/deno
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /opt/parselbox

# Copy project files and install from local source
COPY pyproject.toml uv.lock README.md LICENSE.md ./
COPY parselbox/ parselbox/
RUN uv pip install . --system

# Cache Deno deps, Pyodide snapshot, and Python packages at build time
RUN parselbox --packages numpy,pandas --package-dir /opt/parselbox/packages < /dev/null

CMD ["parselbox", "--transport", "http", "--port", "8080", \
     "--package-dir", "/opt/parselbox/packages"]
