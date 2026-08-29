import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "PaperTrans — Academic Paper Library",
  description: "A local-first library for structure-preserving academic paper translations",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
