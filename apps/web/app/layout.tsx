import type { Metadata } from "next";
import Link from "next/link";
import { DatabaseZap, Sparkles } from "lucide-react";
import { Geist, Geist_Mono } from "next/font/google";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
          <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
            <div className="flex items-center gap-8">
              <Link href="/" className="group flex items-center gap-3" aria-label="UltimateRAG 首页">
                <span className="grid size-9 place-items-center rounded-xl bg-foreground text-background shadow-sm transition-transform group-hover:-rotate-3">
                  <DatabaseZap className="size-4.5" />
                </span>
                <span className="text-[15px] font-semibold tracking-tight">UltimateRAG</span>
              </Link>
              <nav className="hidden items-center gap-1 md:flex" aria-label="主导航">
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/">知识库</Link>
                </Button>
                <Button variant="ghost" size="sm" asChild>
                  <Link href="/#architecture">架构</Link>
                </Button>
              </nav>
            </div>

            <div className="flex items-center gap-2">
              <Badge variant="outline" className="hidden gap-1.5 bg-card/80 sm:inline-flex">
                <Sparkles className="text-primary" />
                V1 · Naive RAG
              </Badge>
            </div>
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
