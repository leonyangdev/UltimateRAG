"use client";

import Link from "next/link";
import {
  BookOpenText,
  Database,
  LoaderCircle,
  MessageSquare,
  PanelLeftClose,
  Plus,
  X,
} from "lucide-react";

import type { ChatSession, KnowledgeBase } from "@/app/lib";
import { Button } from "@/components/ui/button";

interface ChatSidebarProps {
  knowledgeBase: KnowledgeBase | null;
  sessions: ChatSession[];
  activeSessionId: string | null;
  documentCount: number;
  readyCount: number;
  isLoadingSession: boolean;
  isMobileOpen: boolean;
  isDesktopCollapsed: boolean;
  onCloseMobile: () => void;
  onCollapseDesktop: () => void;
  onCreateSession: () => void;
  onOpenSession: (sessionId: string) => void;
}

/**
 * Chat 工作区左侧导航。
 *
 * 组件只负责会话导航与知识库入口，不拥有远程数据状态。桌面端折叠和移动端抽屉共用同一棵
 * DOM，避免两套列表在加载状态或当前会话高亮上产生差异；所有副作用继续由 ChatPage 处理。
 */
export function ChatSidebar({
  knowledgeBase,
  sessions,
  activeSessionId,
  documentCount,
  readyCount,
  isLoadingSession,
  isMobileOpen,
  isDesktopCollapsed,
  onCloseMobile,
  onCollapseDesktop,
  onCreateSession,
  onOpenSession,
}: ChatSidebarProps) {
  return (
    <>
      {/* 移动端遮罩负责关闭抽屉；桌面端侧栏属于正常布局，不遮挡聊天内容。 */}
      {isMobileOpen && (
        <button
          type="button"
          aria-label="关闭会话侧栏"
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-black/25 backdrop-blur-[1px] md:hidden"
        />
      )}

      <aside
        aria-label="会话导航"
        className={`fixed inset-y-0 left-0 z-50 flex w-[260px] flex-col bg-[#f9f9f9] transition-transform duration-200 ease-out md:static md:z-auto dark:bg-[#171717] ${
          isMobileOpen ? "translate-x-0" : "-translate-x-full"
        } ${isDesktopCollapsed ? "md:hidden" : "md:translate-x-0"}`}
      >
        <div className="flex h-14 shrink-0 items-center justify-between px-3">
          <Link
            href="/chat"
            onClick={onCloseMobile}
            className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm font-semibold tracking-tight hover:bg-black/5 dark:hover:bg-white/10"
          >
            <span className="grid size-7 place-items-center rounded-full bg-foreground text-[11px] font-bold text-background">
              UR
            </span>
            UltimateRAG
          </Link>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={isMobileOpen ? onCloseMobile : onCollapseDesktop}
            aria-label={isMobileOpen ? "关闭侧栏" : "折叠侧栏"}
            className="text-muted-foreground hover:bg-black/5 dark:hover:bg-white/10"
          >
            {isMobileOpen ? <X /> : <PanelLeftClose />}
          </Button>
        </div>

        <div className="px-2 pb-2">
          <button
            type="button"
            onClick={onCreateSession}
            disabled={!knowledgeBase || isLoadingSession}
            className="flex w-full items-center gap-3 rounded-lg px-2.5 py-2.5 text-left text-sm font-medium transition-colors hover:bg-black/5 disabled:cursor-not-allowed disabled:opacity-45 dark:hover:bg-white/10"
          >
            <span className="grid size-7 place-items-center rounded-full border border-border bg-background">
              <Plus className="size-4" />
            </span>
            新建对话
          </button>
        </div>

        <nav className="chat-scrollbar min-h-0 flex-1 overflow-y-auto px-2 pb-4">
          <p className="px-2.5 pb-1 pt-3 text-xs font-medium text-muted-foreground">最近</p>
          {isLoadingSession && sessions.length === 0 ? (
            <div className="flex items-center gap-2 px-2.5 py-3 text-sm text-muted-foreground">
              <LoaderCircle className="size-3.5 animate-spin" /> 正在载入会话
            </div>
          ) : sessions.length === 0 ? (
            <p className="px-2.5 py-3 text-sm leading-6 text-muted-foreground">还没有历史对话</p>
          ) : (
            <ul className="space-y-0.5">
              {sessions.map((session) => {
                const isActive = session.id === activeSessionId;
                return (
                  <li key={session.id}>
                    <button
                      type="button"
                      onClick={() => onOpenSession(session.id)}
                      title={session.title}
                      className={`group flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition-colors ${
                        isActive
                          ? "bg-[#ececec] font-medium text-foreground dark:bg-[#2a2a2a]"
                          : "text-foreground/80 hover:bg-black/5 dark:hover:bg-white/10"
                      }`}
                    >
                      <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
                      <span className="truncate">{session.title || "新对话"}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="shrink-0 space-y-1 border-t border-black/5 p-2 dark:border-white/10">
          <Link
            href="/knowledge-bases"
            onClick={onCloseMobile}
            className="flex items-center gap-3 rounded-lg px-2.5 py-2.5 text-sm transition-colors hover:bg-black/5 dark:hover:bg-white/10"
          >
            <span className="grid size-7 place-items-center rounded-full border border-border bg-background">
              <Database className="size-3.5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">管理知识库</span>
              <span className="block truncate text-xs text-muted-foreground">
                {knowledgeBase
                  ? `${knowledgeBase.name} · ${readyCount}/${documentCount} 可检索`
                  : "创建并上传企业文档"}
              </span>
            </span>
            <BookOpenText className="size-3.5 text-muted-foreground" />
          </Link>
        </div>
      </aside>
    </>
  );
}
