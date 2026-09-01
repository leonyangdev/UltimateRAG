"use client";

import Link from "next/link";
import { useState } from "react";
import {
  BookOpenText,
  Database,
  LoaderCircle,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
  X,
} from "lucide-react";

import type { ChatSession, KnowledgeBase } from "@/app/lib";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface ChatSidebarProps {
  knowledgeBase: KnowledgeBase | null;
  sessions: ChatSession[];
  activeSessionId: string | null;
  documentCount: number;
  readyCount: number;
  isLoadingSession: boolean;
  isChatWorking: boolean;
  deletingSessionId: string | null;
  isMobileOpen: boolean;
  isDesktopCollapsed: boolean;
  onCloseMobile: () => void;
  onCollapseDesktop: () => void;
  onExpandDesktop: () => void;
  onCreateSession: () => void;
  onOpenSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => Promise<void>;
}

/**
 * Chat 工作区左侧导航。
 *
 * 组件只负责会话导航、删除确认和知识库入口，不拥有远程会话事实；创建、读取、删除及失败提示
 * 均由 ChatPage 处理。桌面端在 260px 完整导航与 64px 图标轨之间切换，折叠后仍保留展开、
 * 新建会话和知识库管理三个高频入口；移动端始终使用 260px 遮罩抽屉，不继承桌面折叠状态。
 *
 * 会话行把“打开”和“删除”拆成两个同级按钮，避免嵌套交互元素。删除前使用 Radix Dialog
 * 二次确认；当前会话仍在生成时禁用删除，由后端 409 再提供最终一致性保护。
 */
export function ChatSidebar({
  knowledgeBase,
  sessions,
  activeSessionId,
  documentCount,
  readyCount,
  isLoadingSession,
  isChatWorking,
  deletingSessionId,
  isMobileOpen,
  isDesktopCollapsed,
  onCloseMobile,
  onCollapseDesktop,
  onExpandDesktop,
  onCreateSession,
  onOpenSession,
  onDeleteSession,
}: ChatSidebarProps) {
  const [sessionToDelete, setSessionToDelete] = useState<ChatSession | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const isConfirmingDeletion = deletingSessionId === sessionToDelete?.id;

  /** 删除成功后关闭确认框；失败时保留目标，具体原因由页面级错误条展示。 */
  async function confirmSessionDeletion() {
    if (!sessionToDelete || deletingSessionId) return;
    setDeleteError("");
    try {
      await onDeleteSession(sessionToDelete.id);
      setSessionToDelete(null);
    } catch (value) {
      // 页面层会保留全局错误，这里同时在 Dialog 内就近展示 404/409 等原因。
      // 保留确认目标，明确会话没有被乐观移除，并允许生成结束后再次尝试。
      setDeleteError(value instanceof Error ? value.message : "会话删除失败，请稍后重试");
    }
  }

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
        className={`fixed inset-y-0 left-0 z-50 flex w-[260px] shrink-0 flex-col overflow-hidden border-r border-black/[0.04] bg-[#f9f9f9] transition-[transform,width] duration-200 ease-out motion-reduce:transition-none md:static md:z-auto dark:border-white/[0.06] dark:bg-[#171717] ${
          isMobileOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"
        } ${isDesktopCollapsed ? "md:w-16" : "md:w-[260px]"}`}
      >
        {/* 完整导航在移动端始终可见；只有桌面折叠态才隐藏。 */}
        <div
          className={`min-h-0 flex-1 flex-col ${
            isDesktopCollapsed ? "flex md:hidden" : "flex"
          }`}
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
              onClick={onCloseMobile}
              aria-label="关闭侧栏"
              className="text-muted-foreground hover:bg-black/5 md:hidden dark:hover:bg-white/10"
            >
              <X />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onCollapseDesktop}
              aria-label="折叠侧栏"
              aria-expanded={!isDesktopCollapsed}
              className="hidden text-muted-foreground hover:bg-black/5 md:inline-flex dark:hover:bg-white/10"
            >
              <PanelLeftClose />
            </Button>
          </div>

          <div className="px-2 pb-2">
            <button
              type="button"
              onClick={onCreateSession}
              disabled={!knowledgeBase || isLoadingSession || deletingSessionId !== null}
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
                  const isDeleting = deletingSessionId === session.id;
                  const isDeleteBlocked = isActive && isChatWorking;

                  return (
                    <li key={session.id}>
                      <div
                        className={`group flex items-center rounded-lg transition-colors ${
                          isActive
                            ? "bg-[#ececec] font-medium text-foreground dark:bg-[#2a2a2a]"
                            : "text-foreground/80 hover:bg-black/5 dark:hover:bg-white/10"
                        }`}
                      >
                        <button
                          type="button"
                          onClick={() => onOpenSession(session.id)}
                          disabled={isLoadingSession || deletingSessionId !== null}
                          title={session.title || "新对话"}
                          className="flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <MessageSquare className="size-3.5 shrink-0 text-muted-foreground" />
                          <span className="truncate">{session.title || "新对话"}</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setDeleteError("");
                            setSessionToDelete(session);
                          }}
                          disabled={
                            isLoadingSession || deletingSessionId !== null || isDeleteBlocked
                          }
                          aria-label={`删除会话：${session.title || "新对话"}`}
                          title={isDeleteBlocked ? "回答生成完成后才能删除当前会话" : "删除会话"}
                          className="mr-1 grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground opacity-100 outline-none transition hover:bg-black/8 hover:text-destructive focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-30 sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100 dark:hover:bg-white/10"
                        >
                          {isDeleting ? (
                            <LoaderCircle className="size-3.5 animate-spin" />
                          ) : (
                            <Trash2 className="size-3.5" />
                          )}
                        </button>
                      </div>
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
        </div>

        {/* 桌面折叠态保留窄图标轨，用户无需先猜测隐藏在顶栏里的展开入口。 */}
        <div
          className={`hidden min-h-0 flex-1 flex-col items-center gap-2 py-2 ${
            isDesktopCollapsed ? "md:flex" : ""
          }`}
        >
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onExpandDesktop}
            aria-label="展开会话侧栏"
            aria-expanded="false"
            title="展开侧栏"
            className="size-11 rounded-xl hover:bg-black/5 dark:hover:bg-white/10"
          >
            <PanelLeftOpen />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={onCreateSession}
            disabled={!knowledgeBase || isLoadingSession || deletingSessionId !== null}
            aria-label="新建对话"
            title="新建对话"
            className="size-11 rounded-xl hover:bg-black/5 dark:hover:bg-white/10"
          >
            <Plus />
          </Button>
          <div className="flex-1" />
          <Button
            asChild
            variant="ghost"
            size="icon"
            className="size-11 rounded-xl hover:bg-black/5 dark:hover:bg-white/10"
          >
            <Link href="/knowledge-bases" aria-label="管理知识库" title="管理知识库">
              <Database />
            </Link>
          </Button>
        </div>
      </aside>

      <Dialog
        open={sessionToDelete !== null}
        onOpenChange={(open) => {
          if (!open && !isConfirmingDeletion) {
            setSessionToDelete(null);
            setDeleteError("");
          }
        }}
      >
        <DialogContent className="max-w-md" showCloseButton={!isConfirmingDeletion}>
          <DialogHeader>
            <DialogTitle>删除这个会话？</DialogTitle>
            <DialogDescription>
              「{sessionToDelete?.title || "新对话"}」及其中的全部消息和来源快照将被永久删除，
              此操作无法撤销。
            </DialogDescription>
          </DialogHeader>
          {deleteError && (
            <p
              role="alert"
              className="rounded-xl bg-destructive/8 px-3 py-2 text-sm text-destructive"
            >
              {deleteError}
            </p>
          )}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setSessionToDelete(null);
                setDeleteError("");
              }}
              disabled={isConfirmingDeletion}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => void confirmSessionDeletion()}
              disabled={isConfirmingDeletion}
            >
              {isConfirmingDeletion && <LoaderCircle className="animate-spin" />}
              删除会话
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
