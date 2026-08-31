import { NextResponse } from "next/server";
import {
  getPaperTransRuntimeConfig,
  inspectPaperTransRuntime,
} from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const config = getPaperTransRuntimeConfig();
  const readiness = await inspectPaperTransRuntime(config);
  const ready = config.configurationReady;

  return NextResponse.json(
    {
      schemaVersion: 1,
      service: "papertrans",
      version: config.releaseVersion,
      ready,
      readiness: {
        webConfiguration: config.configurationReady,
        cli: readiness.cliReady,
        doclingModels: readiness.doclingModelsReady,
        pdfImport: readiness.pdfImportReady,
      },
    },
    {
      status: ready ? 200 : 503,
      headers: { "cache-control": "no-store" },
    },
  );
}
