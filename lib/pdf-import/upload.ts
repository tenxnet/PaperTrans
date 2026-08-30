export const MAX_PDF_BYTES = 50 * 1024 * 1024;

const MAX_MULTIPART_OVERHEAD_BYTES = 1024 * 1024;
const PDF_IMPORT_UPLOAD_DEADLINE_MS = 60_000;

export const MAX_MULTIPART_BYTES = MAX_PDF_BYTES + MAX_MULTIPART_OVERHEAD_BYTES;

export class PdfImportRequestError extends Error {
  constructor(
    readonly code: "invalid_form" | "pdf_too_large" | "pdf_upload_timeout",
    readonly status: 400 | 408 | 413,
    message: string,
  ) {
    super(message);
  }
}

export async function readBoundedPdfImportForm(request: Request): Promise<FormData> {
  if (request.body === null) {
    throw new PdfImportRequestError("invalid_form", 400, "PDFの送信内容を確認できません");
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  let deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  const deadline = new Promise<never>((_resolve, reject) => {
    deadlineTimer = setTimeout(() => {
      void reader.cancel("PDF upload deadline exceeded").catch(() => undefined);
      reject(new PdfImportRequestError(
        "pdf_upload_timeout",
        408,
        "PDFのアップロードが60秒を超えました",
      ));
    }, PDF_IMPORT_UPLOAD_DEADLINE_MS);
  });

  try {
    for (;;) {
      const { done, value } = await Promise.race([reader.read(), deadline]);
      if (done) break;
      received += value.byteLength;
      if (received > MAX_MULTIPART_BYTES) {
        void reader.cancel("PDF upload exceeds the byte limit").catch(() => undefined);
        throw new PdfImportRequestError("pdf_too_large", 413, "PDFは50MB以下にしてください");
      }
      chunks.push(value);
    }
  } finally {
    if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  }

  const body = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }

  const headers = new Headers(request.headers);
  headers.set("content-length", String(received));
  headers.delete("transfer-encoding");
  try {
    return await new Request(request.url, {
      method: request.method,
      headers,
      body,
    }).formData();
  } catch {
    throw new PdfImportRequestError("invalid_form", 400, "PDFの送信内容を確認できません");
  }
}
