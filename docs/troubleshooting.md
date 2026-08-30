# Troubleshooting

## The web app does not start

Use an explicit loopback address and choose an unused port:

```bash
pnpm dev --hostname 127.0.0.1 --port 3100
```

Then open `http://127.0.0.1:3100`.

## The Web UI says that MCP is stopped

Start the MCP server separately:

```bash
uv sync --extra mcp
.venv/bin/papertrans-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8000
```

The Web UI checks only whether the local MCP port is listening. It cannot verify a client registration or Secure MCP Tunnel.

## ChatGPT cannot reach PaperTrans

Confirm all three layers:

1. The local MCP server is running at `http://127.0.0.1:8000/mcp`.
2. Secure MCP Tunnel is running with the configured PaperTrans profile.
3. The developer-mode app exists in the intended ChatGPT workspace.

Run `tunnel-client doctor --profile papertrans-local --explain` and keep `tunnel-client run --profile papertrans-local` active. If the Tunnel is not listed in ChatGPT, confirm its workspace association and `Read + Use` permissions. See [MCP client setup](mcp-client-setup.md).

Do not expose the unauthenticated local MCP server directly to the internet. Review the data boundary in [MCP translation server](mcp-server.md) before starting a tunnel.

## macOS reports `Operation not permitted` under `Documents`

Open **System Settings → Privacy & Security → Full Disk Access** and allow the current `ChatGPT.app`. Fully restart the app after changing the setting. If an older ChatGPT Classic registration exists, remove the stale entry and add the current app again. Alternatively, keep development repositories in a non-protected directory such as `~/Developer`.

## A completed paper is missing from the library

The library scans persisted output manifests. Confirm that the job has a valid manifest below `output/<job-id>/work/`, that `index.html`, `index.md`, `qa.json`, and `markdown-qa.json` exist below `output/<job-id>/html/`, and that the ZIP exists below `output/<job-id>/`. Call `finalize_translation_html` again to rebuild missing or stale artifacts, then refresh the library.

## `index.md` or `markdown-qa.json` is missing

Call `finalize_translation_html` again after every translation chunk is saved. A successful finalization regenerates HTML and Markdown from the persisted DocumentIR, runs both QA paths, and rebuilds the existing ZIP. If finalization reports that the job predates complete DocumentIR content, prepare the paper again before exporting Markdown.

## The paper layout breaks when opened directly from disk

Regenerate the ZIP by calling `finalize_translation_html` again, then fully extract it before opening `index.html`. PaperTrans records the renderer version in each completed job and rebuilds older HTML and ZIP artifacts during finalization. Do not open `index.html` from inside the ZIP archive because browsers cannot resolve its sibling `assets/` directory there. The same extracted directory also contains `index.md` and `markdown-qa.json`; keep them beside `assets/` so relative image links continue to work.
