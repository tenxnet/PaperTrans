import type { Metadata } from "next";
import "./styles.css";

export const metadata: Metadata = {
  title: "PaperTrans MVP",
  description: "Local academic PDF translation pipeline",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ja">
      <body>{children}</body>
    </html>
  );
}
