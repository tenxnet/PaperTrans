# Provider settings / Provider設定

PaperTrans v0.1 supports two translation workflows. Open **Provider settings** in the Web UI sidebar to select one. The selection, Codex model, and reasoning effort are stored in the current browser profile.

PaperTrans v0.1は2つの翻訳経路に対応します。Web UIのサイドバーから**Provider設定**を開いて選択してください。選択内容、Codexモデル、推論強度は現在のブラウザプロファイルに保存されます。

## ChatGPT Connector

1. Start the local MCP server and Secure MCP Tunnel.
2. Select **ChatGPT Connector**.
3. Enter an arXiv ID under **New translation**.
4. Copy the request, open ChatGPT, and send it in the connected conversation.

ChatGPT ConnectorにはローカルMCPサーバーとSecure MCP Tunnelが必要です。PaperTransはChatGPTの会話を自動開始しません。詳しい設定とデータ境界は[ChatGPT translation worker](chatgpt-worker.md)を参照してください。

## Codex CLI

1. Select **Codex CLI**.
2. Choose `gpt-5.6-luna` or `gpt-5.3-codex-spark` and a reasoning effort.
3. Enter an arXiv ID under **New translation**.
4. Copy the generated command and run it from the PaperTrans repository root.

Codex CLIはTunnel不要です。v0.1のWeb UIはコマンドを生成しますが、自動実行はしません。既定値は`gpt-5.6-luna`と`low`です。

## Current boundary / 現在の境界

- Changing the provider affects new translation requests only; existing artifacts are unchanged.
- Settings are browser-local, so another browser or profile may have a different selection.
- The OpenAI API provider and Web UI-managed background execution are not implemented in v0.1.
