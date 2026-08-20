# Parselbox Basics

Core examples demonstrating Parselbox sandbox features.

## Files

| File              | Description                                                           |
| ----------------- | --------------------------------------------------------------------- |
| `basics.py`       | Core execution, state persistence, globals, errors, packages, network |
| `bridges.py`      | All bridge types: plain class, nested, HTTP, GraphQL, Shell, MCP, Pydantic |
| `toolkit.py`      | `sbx.*` sandbox utilities (info, search, inspect, preview)            |
| `filesystem.py`   | Files, mounts, output persistence, skills discovery                   |
| `javascript.py`   | `js()`, `require()`, TypeScript, npm, cross-language |
| `bash.py`          | Shell commands, pipes, grep, find, shared filesystem                  |
| `tasks.py`        | Background `.task()` API: progress, logs, send/recv, cancel, parallel |
| `hooks.py`        | Lifecycle hooks: audit logging, policy enforcement                    |
| `display.py`      | Inline UI: `display()` HTML widgets rendered in MCP Apps hosts        |
| `serve.py`        | HTTP server: static files, API handlers, file upload, live reload     |
