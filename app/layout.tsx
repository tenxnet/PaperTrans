import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "PaperTrans — 学術論文ライブラリ",
  description: "arXiv論文を構造のまま日本語で読むローカルライブラリ",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
