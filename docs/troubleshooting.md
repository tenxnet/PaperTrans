# Troubleshooting

## The web app does not start

Use an explicit loopback address and choose an unused port:

```bash
pnpm dev --hostname 127.0.0.1 --port 3100
```

Then open `http://127.0.0.1:3100`.

## ChatGPT Connector shows that MCP is stopped

Start the MCP server separately:

```bash
uv sync --extra chatgpt --extra test
.venv/bin/papertrans-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The Web UI checks only whether the local MCP port is listening. It cannot verify the Secure MCP Tunnel or the Connector registration inside ChatGPT.

## ChatGPT cannot reach PaperTrans

Confirm all three layers:

1. The local MCP server is running at `http://127.0.0.1:8000/mcp`.
2. Secure MCP Tunnel is running with the configured PaperTrans profile.
3. The Connector exists in the intended ChatGPT workspace.

Do not expose the unauthenticated local MCP server directly to the internet. Review the data boundary in [MCP translation server](mcp-server.md) before starting a tunnel.

## macOS reports `Operation not permitted` under `Documents`

Open **System Settings → Privacy & Security → Full Disk Access** and allow the current `ChatGPT.app`. Fully restart the app after changing the setting. If an older ChatGPT Classic registration exists, remove the stale entry and add the current app again. Alternatively, keep development repositories in a non-protected directory such as `~/Developer`.

## A completed paper is missing from the library

The library scans persisted output manifests. Confirm that the job has a valid manifest below `output/<job-id>/work/` and that the generated HTML exists below `output/<job-id>/html/`. Refresh the library after the files are complete.

## The paper layout breaks when opened directly from disk

Use the PaperTrans web app instead of opening `index.html` through a `file://` URL. The local server supplies asset paths and embedding behavior that direct file access may not preserve consistently across browsers.
