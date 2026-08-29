import type { Metadata } from "next";
import Link from "next/link";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: "UltimateRAG · 企业知识工作台",
    template: "%s · UltimateRAG",
  },
  description: "从可信文档到可追溯回答的企业级 RAG 工作台",
};

/**
 * 提供全站品牌导航与内容宽度约束。
 * 页面业务状态由各自的 Client Component 管理，布局层不发起 API 请求。
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans`}>
        <header className="sticky top-0 z-40 border-b border-border/70 bg-background/88 backdrop-blur-xl">
          <div className="mx-auto flex h-16 max-w-7xl items-center gap-10 px-4 sm:px-6 lg:px-8">
            <Link
              href="/"
              className="text-lg font-medium tracking-tight"
              aria-label="UltimateRAG 首页"
            >
              UltimateRAG
            </Link>
            {/* 导航链接保持主流产品的纯文字形态：hover 只过渡文字颜色，
                不使用按钮组件的背景填充，避免链接看起来像可点击的按钮。 */}
            <nav className="hidden items-center gap-7 md:flex" aria-label="主导航">
              <Link
                href="/knowledge-bases"
                className="text-lg font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                知识库
              </Link>
              <Link
                href="/chat"
                className="text-lg font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                知识问答
              </Link>
            </nav>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
