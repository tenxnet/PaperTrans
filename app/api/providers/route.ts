import { createConnection } from "node:net";
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

export async function GET() {
  const port = mcpPort();
  const online = await isListening(port);
  return NextResponse.json({
    defaultProvider: "chatgpt_connector",
    providers: {
      chatgpt_connector: {
        enabled: true,
        mcpServer: online ? "online" : "offline",
        mcpUrl: `http://${MCP_HOST}:${port}/mcp`,
        startsInApp: false,
      },
    },
  });
}
