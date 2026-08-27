import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "UltimateRAG",
  description: "可演进的企业级 RAG 平台",
};

/**
 * 提供全站品牌导航与内容宽度约束。
 * 页面业务状态由各自的 Client Component 管理，布局层不发起 API 请求。
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <header className="topbar">
          <Link href="/" className="brand">
            <span className="brandMark">U</span>
            <span>UltimateRAG</span>
          </Link>
          <span className="version">V1 · Naive RAG</span>
        </header>
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
