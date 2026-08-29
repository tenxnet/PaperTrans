# MCP client setup

[日本語](#日本語) | [English](#english)

PaperTrans prepares translation jobs and stores the results. A connected MCP client performs the translation. Local clients can connect directly; ChatGPT needs OpenAI Secure MCP Tunnel.

## 日本語

### 1. PaperTransを起動する

```bash
uv sync --extra mcp
.venv/bin/papertrans-mcp --host 127.0.0.1 --port 8000
```

MCPエンドポイントは`http://127.0.0.1:8000/mcp`です。別のターミナルでWebアプリも起動します。

```bash
pnpm dev --hostname 127.0.0.1
```

### 2A. ローカルMCPクライアント

クライアントのMCP設定で、Streamable HTTP URLとして`http://127.0.0.1:8000/mcp`を登録します。接続後に`list_translation_jobs`などのPaperTransツールが見えれば準備完了です。Tunnelは不要です。

stdioを使うクライアントでは、リポジトリを作業ディレクトリにして`.venv/bin/papertrans-mcp --transport stdio`を起動コマンドとして登録できます。設定ファイルの形式はクライアントごとに異なります。

### 2B. ChatGPT + Secure MCP Tunnel

ChatGPTからローカルMCPへ接続する場合だけ、この手順が必要です。OpenAIの[Secure MCP Tunnel公式ガイド](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)も確認してください。

1. [OpenAI PlatformのTunnels設定](https://platform.openai.com/settings/organization/tunnels)でTunnelを作成し、使用するChatGPT workspaceを関連付けます。
2. runtime API keyと`tunnel_id`を取得します。秘密情報はコミットせず、Git管理外の`.env.local`などへ保存してください。
3. Tunnels設定画面または[最新のtunnel-client release](https://github.com/openai/tunnel-client/releases/latest)からクライアントを入手します。特定バージョンへ固定しません。
4. PaperTrans MCPを起動したまま、次を実行します。

```bash
set -a
source .env.local
set +a

tunnel-client init \
  --sample sample_mcp_remote_no_auth \
  --profile papertrans-local \
  --tunnel-id "$CONTROL_PLANE_TUNNEL_ID" \
  --mcp-server-url "http://127.0.0.1:8000/mcp"

tunnel-client doctor --profile papertrans-local --explain
tunnel-client run --profile papertrans-local
```

5. `tunnel-client run`を動かしたまま、ChatGPTのPluginsでdeveloper-mode appを作成し、Connectionに**Tunnel**を選びます。作成したTunnelを選択し、PaperTransツールが表示されることを確認します。

Tunnelが候補に出ない場合は、対象workspaceとの関連付けとTunnelsの`Read + Use`権限を確認してください。`tunnel-client run`は、app検出中も翻訳中も起動しておく必要があります。

### 3. 翻訳する

1. Web UIの「新しい翻訳」でarXiv IDを登録します。
2. 「ワーカー依頼をコピー」を押します。
3. コピーした依頼文を、PaperTransへ接続済みのMCPクライアントへ送ります。
4. 完了後、Web UIを更新してライブラリから論文を開きます。

中断した場合もジョブはローカルに残ります。同じ依頼文をもう一度送るか、クライアントへ`list_translation_jobs`で未完了ジョブを再開するよう依頼してください。

## English

### 1. Start PaperTrans

Install the `mcp` extra, run `papertrans-mcp` on `127.0.0.1:8000`, and start the web app as shown above. The Streamable HTTP endpoint is `http://127.0.0.1:8000/mcp`.

### 2A. Local MCP client

Register that endpoint as a Streamable HTTP MCP server in your client. The connection is ready when PaperTrans tools such as `list_translation_jobs` are visible. No tunnel is required. For a stdio client, register `.venv/bin/papertrans-mcp --transport stdio` with the repository as its working directory.

### 2B. ChatGPT with Secure MCP Tunnel

1. Create a tunnel in [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels) and associate the intended ChatGPT workspace.
2. Store the runtime API key and `tunnel_id` outside Git, for example in the ignored `.env.local` file.
3. Install the latest `tunnel-client`; do not pin the guide to one release.
4. Initialize, diagnose, and run the HTTP profile with the commands in the Japanese section above. Keep `tunnel-client run` running.
5. In ChatGPT Plugins, create a developer-mode app, choose **Tunnel** under Connection, select the tunnel, and verify that the PaperTrans tools appear.

See the [official Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) for current permissions, network requirements, and troubleshooting.

### 3. Translate

Create a job under **New translation**, copy its **Worker request**, and send that request to the connected MCP client. Refresh the PaperTrans library after the client finishes. Persisted jobs can be resumed by sending the same request again or asking the client to use `list_translation_jobs`.

## Data boundary

An MCP client can read paper text and job state and can save translations for PaperTrans jobs. Connect only a client and tunnel workspace you trust. Never expose the unauthenticated loopback MCP server directly to the public internet; see [MCP translation server](mcp-server.md) and [SECURITY.md](../SECURITY.md).
