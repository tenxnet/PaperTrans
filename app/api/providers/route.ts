import { createConnection } from "node:net";
import { spawn } from "node:child_process";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MCP_HOST = "127.0.0.1";

function mcpPort() {
  const configured = Number.parseInt(process.env.PAPERTRANS_MCP_PORT ?? "8000", 10);
  return Number.isInteger(configured) && configured > 0 && configured <= 65_535 ? configured : 8000;
}

function isListening(port: number) {
  return new Promise<boolean>((resolve) => {
    const socket = createConnection({ host: MCP_HOST, port });
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(400);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

function isRunnable(command: string) {
  return new Promise<boolean>((resolve) => {
    const child = spawn(command, ["--version"], { stdio: "ignore" });
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      resolve(value);
    };
    const timer = setTimeout(() => finish(false), 1_500);
    child.once("error", () => finish(false));
    child.once("exit", (code) => finish(code === 0));
  });
}

export async function GET() {
  const port = mcpPort();
  const codexCommand = process.env.PAPERTRANS_CODEX_BIN?.trim() || "codex";
  const [online, codexAvailable] = await Promise.all([
    isListening(port),
    isRunnable(codexCommand),
  ]);
  return NextResponse.json({
    defaultProvider: "chatgpt_connector",
    providers: {
      chatgpt_connector: {
        enabled: true,
        mcpServer: online ? "online" : "offline",
        mcpUrl: `http://${MCP_HOST}:${port}/mcp`,
        startsInApp: false,
      },
      codex_cli: {
        enabled: true,
        command: codexCommand,
        status: codexAvailable ? "available" : "unavailable",
        startsInApp: false,
      },
    },
  });
}
