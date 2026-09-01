import type { Metadata } from "next";
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
    default: "UltimateRAG",
    template: "%s · UltimateRAG",
  },
  description: "与你的企业知识库自然对话，并随时核验回答来源。",
};

/**
 * 提供全站字体、主题和页面挂载点。
 *
 * Chat 页面自己拥有全高应用壳、会话侧栏和顶部栏；如果 RootLayout 再渲染一套传统网站
 * 导航，就会破坏聊天产品对视口高度和滚动区域的控制。知识库管理页同样保留自己的返回
 * 入口，因此这里只负责不会随路由改变的全局能力，不发起 API 请求。
 */
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className={`${geistSans.variable} ${geistMono.variable} font-sans`}>
        <main>{children}</main>
      </body>
    </html>
  );
}
