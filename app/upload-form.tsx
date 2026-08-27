"use client";

import { useState } from "react";

export function UploadForm() {
  const [message, setMessage] = useState("");

  async function submit(formData: FormData) {
    setMessage("取込中…");
    const response = await fetch("/api/papers/import", { method: "POST", body: formData });
    const result = (await response.json()) as { slug?: string; error?: string };
    setMessage(response.ok ? `${result.slug} の処理を開始しました` : result.error ?? "取込に失敗しました");
  }

  return (
    <form className="upload" action={submit}>
      <label>
        <span>PDFを追加</span>
        <input name="paper" type="file" accept="application/pdf,.pdf" required />
      </label>
      <button type="submit">取込＋翻訳</button>
      {message && <small>{message}</small>}
    </form>
  );
}
