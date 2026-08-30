import { lstat } from "node:fs/promises";
import path from "node:path";
import modelLock from "@/docling-models.lock.json";
import packageManifest from "@/package.json";

type RuntimeEnvironment = Readonly<Record<string, string | undefined>>;

export type PaperTransRuntimeConfig = Readonly<{
  repoRoot: string;
  dataRoot: string;
  outputRoot: string;
  cliPath: string;
  doclingModelRoot: string;
  releaseVersion: string;
  mcpPort: number;
  releaseReady: boolean;
  configurationReady: boolean;
}>;

export type PaperTransRuntimeReadiness = Readonly<{
  cliReady: boolean;
  doclingModelsReady: boolean;
  pdfImportReady: boolean;
}>;

const CHILD_ENVIRONMENT_KEYS = [
  "ALL_PROXY",
  "HOME",
  "HTTP_PROXY",
  "HTTPS_PROXY",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "NO_PROXY",
  "PATH",
  "PYTHONIOENCODING",
  "PYTHONUTF8",
  "REQUESTS_CA_BUNDLE",
  "SSL_CERT_DIR",
  "SSL_CERT_FILE",
  "TMP",
  "TMPDIR",
  "TEMP",
  "TOKENIZERS_PARALLELISM",
  "VIRTUAL_ENV",
  "XDG_CACHE_HOME",
  "XDG_CONFIG_HOME",
  "all_proxy",
  "http_proxy",
  "https_proxy",
  "no_proxy",
] as const;

const DOCLING_TUNING_KEYS = [
  "PAPERTRANS_DOCLING_DOCUMENT_TIMEOUT",
  "PAPERTRANS_DOCLING_PARSER_THREADS",
  "PAPERTRANS_DOCLING_WORKER_TIMEOUT",
] as const;

const DEFAULT_MCP_PORT = 8000;
const EXECUTABLE_BITS = 0o111;
const RELEASE_PATTERN = /^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$/;

function configuredString(value: string | undefined) {
  if (value === undefined || value.trim() === "") {
    return { value: null, valid: true } as const;
  }
  const normalized = value.trim();
  return {
    value: normalized.includes("\0") ? null : normalized,
    valid: !normalized.includes("\0"),
  } as const;
}

function resolveConfiguredPath(
  configured: string | undefined,
  fallback: string,
  relativeTo: string,
) {
  const parsed = configuredString(configured);
  const selected = parsed.value ?? fallback;
  return {
    value: path.resolve(relativeTo, selected),
    valid: parsed.valid,
  } as const;
}

function resolveMcpPort(configured: string | undefined) {
  const parsed = configuredString(configured);
  if (parsed.value === null) {
    return { value: DEFAULT_MCP_PORT, valid: parsed.valid } as const;
  }
  if (!/^\d{1,5}$/.test(parsed.value)) {
    return { value: DEFAULT_MCP_PORT, valid: false } as const;
  }
  const value = Number(parsed.value);
  return {
    value: value >= 1 && value <= 65_535 ? value : DEFAULT_MCP_PORT,
    valid: value >= 1 && value <= 65_535,
  } as const;
}

/**
 * Resolve launcher-owned configuration from the trusted server environment.
 * Relative values are always anchored to the repository root, never to request
 * data. The function intentionally returns no raw environment values.
 */
export function getPaperTransRuntimeConfig(
  environment: RuntimeEnvironment = process.env,
): PaperTransRuntimeConfig {
  // Local releases intentionally resolve runtime-owned data outside the Next
  // bundle; do not make Turbopack trace the repository as an application asset.
  const cwd = path.resolve(/* turbopackIgnore: true */ process.cwd());
  const repo = resolveConfiguredPath(environment.PAPERTRANS_REPO_ROOT, cwd, cwd);
  const data = resolveConfiguredPath(
    environment.PAPERTRANS_DATA_ROOT,
    path.join(repo.value, "data"),
    repo.value,
  );
  const output = resolveConfiguredPath(
    environment.PAPERTRANS_OUTPUT_ROOT,
    path.join(repo.value, "output"),
    repo.value,
  );
  const cli = resolveConfiguredPath(
    environment.PAPERTRANS_CLI,
    path.join(repo.value, ".venv", "bin", "papertrans"),
    repo.value,
  );
  const models = resolveConfiguredPath(
    environment.PAPERTRANS_DOCLING_ARTIFACTS_PATH,
    path.join(data.value, "models", "docling"),
    repo.value,
  );
  const port = resolveMcpPort(environment.PAPERTRANS_MCP_PORT);
  const configuredRelease = configuredString(environment.PAPERTRANS_VERSION);
  const packageRelease = RELEASE_PATTERN.test(packageManifest.version)
    ? packageManifest.version
    : "unknown";
  const releaseReady = packageRelease !== "unknown"
    && modelLock.release === `v${packageRelease}`
    && configuredRelease.valid
    && (configuredRelease.value === null || configuredRelease.value === packageRelease);

  return Object.freeze({
    repoRoot: repo.value,
    dataRoot: data.value,
    outputRoot: output.value,
    cliPath: cli.value,
    doclingModelRoot: models.value,
    releaseVersion: packageRelease,
    mcpPort: port.value,
    releaseReady,
    configurationReady: repo.valid
      && data.valid
      && output.valid
      && cli.valid
      && models.valid
      && port.valid
      && releaseReady,
  });
}

/**
 * Build the deliberately small environment passed to PaperTrans child jobs.
 *
 * The Web server can be started outside the one-command launcher, so copying
 * all of `process.env` here could leak unrelated credentials or re-enable
 * Python/Node loader injection. Only operating-system basics, CA settings, and
 * the documented numeric Docling tuning controls are inherited.
 */
export function paperTransChildEnvironment(
  config: PaperTransRuntimeConfig,
  environment: RuntimeEnvironment = process.env,
): NodeJS.ProcessEnv {
  const child: NodeJS.ProcessEnv = {
    NODE_ENV: environment.NODE_ENV === "development" ? "development" : "production",
  };
  for (const key of CHILD_ENVIRONMENT_KEYS) {
    const value = environment[key];
    if (value !== undefined && !value.includes("\0")) child[key] = value;
  }
  for (const key of DOCLING_TUNING_KEYS) {
    const value = environment[key];
    if (value !== undefined && /^\d+(?:\.\d+)?$/.test(value)) child[key] = value;
  }
  Object.assign(child, {
    HF_DATASETS_OFFLINE: "1",
    HF_HUB_DISABLE_TELEMETRY: "1",
    HF_HUB_OFFLINE: "1",
    PAPERTRANS_CLI: config.cliPath,
    PAPERTRANS_DATA_ROOT: config.dataRoot,
    PAPERTRANS_DOCLING_ARTIFACTS_PATH: config.doclingModelRoot,
    PAPERTRANS_MCP_HOST: "127.0.0.1",
    PAPERTRANS_MCP_PORT: String(config.mcpPort),
    PAPERTRANS_OUTPUT_ROOT: config.outputRoot,
    PAPERTRANS_REPO_ROOT: config.repoRoot,
    PAPERTRANS_VERSION: config.releaseVersion,
    TRANSFORMERS_OFFLINE: "1",
  });
  return child;
}

async function isExecutableRegularFile(filename: string): Promise<boolean> {
  try {
    const info = await lstat(filename);
    return info.isFile()
      && !info.isSymbolicLink()
      && (info.mode & EXECUTABLE_BITS) !== 0;
  } catch {
    return false;
  }
}

function safeModelParts(relative: unknown): string[] | null {
  if (typeof relative !== "string" || !relative || path.posix.isAbsolute(relative)) return null;
  const parts = relative.split("/");
  if (parts.some((part) => !part || part === "." || part === ".." || part.includes("\\"))) {
    return null;
  }
  return parts;
}

async function isPinnedRegularFile(
  modelRoot: string,
  relative: unknown,
  expectedSize: unknown,
): Promise<boolean> {
  const parts = safeModelParts(relative);
  if (parts === null || !Number.isSafeInteger(expectedSize) || Number(expectedSize) <= 0) return false;
  try {
    let current = modelRoot;
    const rootInfo = await lstat(current);
    if (!rootInfo.isDirectory() || rootInfo.isSymbolicLink()) return false;
    for (const component of parts.slice(0, -1)) {
      current = path.join(current, component);
      const directoryInfo = await lstat(current);
      if (!directoryInfo.isDirectory() || directoryInfo.isSymbolicLink()) return false;
    }
    const fileInfo = await lstat(path.join(current, parts.at(-1)!));
    return fileInfo.isFile()
      && !fileInfo.isSymbolicLink()
      && fileInfo.size === expectedSize;
  } catch {
    return false;
  }
}

async function pinnedDoclingModelsReady(modelRoot: string): Promise<boolean> {
  if (!Array.isArray(modelLock.files) || modelLock.files.length === 0) return false;
  const checks = await Promise.all(modelLock.files.map((entry) => (
    isPinnedRegularFile(modelRoot, entry.path, entry.size)
  )));
  return checks.every(Boolean);
}

export async function inspectPaperTransRuntime(
  config: PaperTransRuntimeConfig = getPaperTransRuntimeConfig(),
): Promise<PaperTransRuntimeReadiness> {
  const [cliReady, doclingModelsReady] = await Promise.all([
    isExecutableRegularFile(config.cliPath),
    pinnedDoclingModelsReady(config.doclingModelRoot),
  ]);
  return Object.freeze({
    cliReady,
    doclingModelsReady,
    pdfImportReady: config.configurationReady && cliReady && doclingModelsReady,
  });
}
