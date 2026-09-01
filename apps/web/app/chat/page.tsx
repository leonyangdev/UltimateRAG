"use client";

import Link from "next/link";
import {
  FormEvent,
  KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import {
  ArrowUp,
  BookOpenText,
  Bot,
  ChevronDown,
  CircleAlert,
  FileText,
  LoaderCircle,
  Menu,
  Plus,
  Search,
  SlidersHorizontal,
  Sparkles,
  Square,
} from "lucide-react";

import {
  api,
  API_URL,
  ChatSession,
  ChatSessionDetail,
  DocumentItem,
  KnowledgeBase,
  RAGMessage as RAGMessageType,
  RetrievalExplainResponse,
  RetrievalMode,
  RetrievalResult,
  RetrievalTrace,
  toRAGMessages,
} from "@/app/lib";
import { RAGMessage } from "@/components/rag-message";
import { RetrievalEvidence } from "@/components/retrieval-evidence";
import { ChatSidebar } from "@/components/chat-sidebar";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Mode = "chat" | "retrieval";

/**
 * ChatGPT 风格的统一知识问答工作区。
 *
 * 设计背景：
 *   此前问答与文档管理共用 /knowledge-bases/[id] 工作台，问答范围由 URL 决定，
 *   想换一个知识库提问就必须先跳回列表页。本页面把「选择问答范围」变成页面内的
 *   交互：输入框上方的知识库选择器负责切换，URL 始终保持 /chat 不变。
 *
 * 职责边界：
 *   本页面只负责问答与检索调试。文档的上传、状态管理在
 *   /knowledge-bases/[id] 文档工作台完成；这里只读取文档状态用于判断
 *   「当前知识库是否有可检索内容」，不提供修改入口。
 *
 * 关键行为：
 *   - “新聊天”首先是浏览器中的未持久化草稿；只有用户第一次发送消息时才创建数据库会话，
 *     因此进入页面或反复点击新建不会在历史列表制造空记录。
 *   - useChat 使用独立的视图 ID，而不是直接绑定数据库 session_id。草稿首次持久化时保持
 *     同一个 Chat 实例，避免刚加入的用户消息因 session_id 从 null 变为 UUID 而被清空。
 *   - RAG 请求体携带 knowledge_base_id，后端按该范围检索，与服务端的知识库
 *     隔离约束保持单一事实来源（URL 不参与范围决定）。
 *
 * 信息架构：
 *   左侧栏承载新建会话、历史会话和知识库管理入口；中间区域只保留顶部范围选择、消息流与
 *   悬浮 Composer。检索模式、Rerank 和文档过滤属于低频高级能力，因此收进顶部二级面板，
 *   不再长期占用聊天首屏。移动端把左侧栏转换为带遮罩的抽屉，消息流仍拥有唯一滚动容器。
 *
 * 交互约束：
 *   切换知识库会中止旧流并进入该知识库的新草稿；选择历史会话则从 PostgreSQL 恢复完整
 *   上下文和 Retrieval 快照。删除最后一个历史会话也回到草稿态，不创建替代空记录。
 *   Composer 使用 Enter 发送、Shift+Enter 换行并在 200px 内自动增高，保证长问题可编辑但
 *   不会挤掉整个回答区域。
 */
export default function ChatPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSession | null>(null);
  const [initialSessionMessages, setInitialSessionMessages] = useState<RAGMessageType[]>([]);
  // Chat View ID 只表达“当前屏幕是哪一次对话视图”，不等同于 PostgreSQL session_id。
  // 草稿首次发送后数据库 ID 会出现，但视图 ID 保持不变，AI SDK 因此不会重建并丢失流式消息。
  const [chatViewId, setChatViewId] = useState("chat-view-initial");
  const [isSessionOperationPending, setIsSessionOperationPending] = useState(false);
  // 与文档加载相同，历史列表加载态由「当前选择」和「已经完成加载的归属」推导，避免在
  // Effect 入口同步 setState 触发 React 级联渲染；打开历史、首次创建则使用独立操作态。
  const [sessionsLoadedForId, setSessionsLoadedForId] = useState<string | null>(null);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  // 记录 documents 当前归属的知识库。「已选择但尚未加载完成」由二者差异推导，
  // 避免在 effect 体内同步 setState（React 新规范不推荐，会触发级联渲染）。
  const [loadedForId, setLoadedForId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>([]);
  const [retrievalTrace, setRetrievalTrace] = useState<RetrievalTrace | null>(null);
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("hybrid");
  const [enableQueryRewrite, setEnableQueryRewrite] = useState(true);
  const [enableRerank, setEnableRerank] = useState(true);
  const [enableParentExpansion, setEnableParentExpansion] = useState(true);
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [mode, setMode] = useState<Mode>("chat");
  const [pageError, setPageError] = useState("");
  const [isLoadingKnowledgeBases, setIsLoadingKnowledgeBases] = useState(true);
  const [isRetrieving, setIsRetrieving] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [isDesktopSidebarCollapsed, setIsDesktopSidebarCollapsed] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const chatViewSequenceRef = useRef(0);
  const chatViewKnowledgeBaseRef = useRef<string | null>(null);
  // React State 在下一次渲染前不会立即生效。同步 Ref 专门封住双击发送、连续 Enter 等
  // 同一事件循环内的重复提交，确保一个 Draft 最多发起一次会话创建请求。
  const isCreatingSessionRef = useRef(false);

  // Transport 只创建一次。每次提问的 question 放进 sendMessage options，避免闭包保存旧输入，
  // 也让 AI SDK 继续负责取消、请求状态机和 Message Part 的增量合并。
  const transport = useMemo(
    () => new DefaultChatTransport<RAGMessageType>({ api: `${API_URL}/api/chat/stream` }),
    [],
  );

  // 只有进入另一个草稿或打开历史会话时才改变 chatViewId。首次发送创建数据库会话时
  // activeSession 会从 null 变为 UUID，但该 ID 不参与 useChat 重建条件。
  const {
    messages,
    sendMessage,
    status: chatStatus,
    error: chatError,
    stop,
  } = useChat<RAGMessageType>({
    id: chatViewId,
    messages: initialSessionMessages,
    transport,
  });

  const isChatWorking = chatStatus === "submitted" || chatStatus === "streaming";
  const hasReadyDocument = documents.some((document) => document.status === "READY");
  const readyDocuments = documents.filter((document) => document.status === "READY");
  const readyCount = readyDocuments.length;
  const selectedKnowledgeBase = knowledgeBases.find((base) => base.id === selectedId) ?? null;
  // 选中了知识库但文档列表还不属于它，说明正在加载；未选中时无需加载。
  const isLoadingDocuments = selectedId !== null && loadedForId !== selectedId;
  const isLoadingSessionHistory =
    selectedId !== null && sessionsLoadedForId !== selectedId;
  const isLoadingSession = isSessionOperationPending || isLoadingSessionHistory;

  /**
   * 进入一个新的未持久化聊天视图。
   *
   * 该动作只修改浏览器状态，不调用会话 POST。``chatViewId`` 的递增会让 AI SDK 创建干净的
   * Chat 实例；知识库 ID 进入标识仅用于调试可读性，不作为后端会话事实。
   */
  const enterDraftChat = useCallback((knowledgeBaseId: string) => {
    chatViewSequenceRef.current += 1;
    chatViewKnowledgeBaseRef.current = knowledgeBaseId;
    setChatViewId(`draft:${knowledgeBaseId}:${chatViewSequenceRef.current}`);
    setActiveSession(null);
    setInitialSessionMessages([]);
    setQuestion("");
    setRetrievalResults([]);
    setRetrievalTrace(null);
    setPageError("");
    if (composerRef.current) composerRef.current.style.height = "auto";
  }, []);

  useEffect(() => {
    let isActive = true;
    void api<KnowledgeBase[]>("/api/knowledge-bases")
      .then((values) => {
        if (!isActive) return;
        setKnowledgeBases(values);
        // 从知识库工作台进入时优先使用 URL 指定范围；直接打开 /chat 则回退到第一项。
        // 参数只能在已加载列表中匹配，不能让任意外部字符串进入后续请求路径。
        const requestedId = new URLSearchParams(window.location.search).get("knowledge_base_id");
        const initialId = values.some((item) => item.id === requestedId)
          ? requestedId
          : values[0]?.id ?? null;
        setSelectedId((current) => current ?? initialId);
      })
      .catch((value: unknown) => {
        if (isActive) setPageError(value instanceof Error ? value.message : "知识库加载失败");
      })
      .finally(() => {
        if (isActive) setIsLoadingKnowledgeBases(false);
      });

    // 页面离开后忽略旧请求，避免异步响应继续修改已经卸载的页面状态。
    return () => {
      isActive = false;
    };
  }, []);

  useEffect(() => {
    // 没有选中知识库时（初始加载或系统中没有知识库）无需请求文档。
    if (!selectedId) return;

    let isActive = true;
    void api<DocumentItem[]>(`/api/knowledge-bases/${selectedId}/documents`)
      .then((values) => {
        if (!isActive) return;
        setDocuments(values);
        // 文档删除或重新处理后移除已经不再 READY 的过滤项，防止隐藏的旧 ID 让检索返回空集。
        const readyIds = new Set(
          values.filter((document) => document.status === "READY").map((document) => document.id),
        );
        setSelectedDocumentIds((current) => current.filter((id) => readyIds.has(id)));
        setLoadedForId(selectedId);
      })
      .catch((value: unknown) => {
        if (!isActive) return;
        // 加载失败时同样推进 loadedForId，让界面落到「无可检索文档」状态，
        // 而不是永远停留在加载中；具体原因由错误提示条说明。
        setDocuments([]);
        setLoadedForId(selectedId);
        setPageError(value instanceof Error ? value.message : "文档状态加载失败");
      });

    // 知识库切换时忽略旧请求，防止上一个知识库的文档状态污染当前选择。
    return () => {
      isActive = false;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    let isActive = true;

    // 进入知识库只加载已有历史，并在本地展示新聊天草稿。GET 在 React Strict Mode 下
    // 即使重复执行也没有副作用；不再用 Ref 抑制第二次 Effect，避免第一次请求被 cleanup
    // 标记失效后没有任何请求可以提交结果。
    if (chatViewKnowledgeBaseRef.current !== selectedId) enterDraftChat(selectedId);
    void api<ChatSession[]>(`/api/knowledge-bases/${selectedId}/chat-sessions`)
      .then((history) => {
        if (!isActive) return;
        setSessions(history);
      })
      .catch((value: unknown) => {
        if (isActive) setPageError(value instanceof Error ? value.message : "历史会话加载失败");
      })
      .finally(() => {
        if (isActive) setSessionsLoadedForId(selectedId);
      });

    return () => {
      isActive = false;
    };
  }, [enterDraftChat, selectedId]);

  useEffect(() => {
    // 流式阶段每次 Message Part 增长后跟随到底部，让新 token 始终保持可见。
    messagesEndRef.current?.scrollIntoView({
      behavior: chatStatus === "streaming" ? "smooth" : "auto",
    });
  }, [messages, chatStatus]);

  /**
   * 切换问答目标知识库。
   * 消息清空由 chatViewId 变化自动完成；这里负责清空与旧知识库绑定的
   * 检索结果和错误提示，并在流式进行中时中止旧请求，避免 token 写入已废弃的会话。
   */
  function selectKnowledgeBase(id: string) {
    if (id === selectedId || deletingSessionId || isLoadingSession) return;
    if (isChatWorking) stop();
    setSelectedId(id);
    setSessions([]);
    setSelectedDocumentIds([]);
    setIsSettingsOpen(false);
    enterDraftChat(id);
  }

  /**
   * 进入 ChatGPT 式新聊天草稿，不创建数据库记录。
   *
   * 已经位于空草稿时重复点击是幂等操作，只关闭移动抽屉并聚焦输入框；从历史会话进入时才
   * 递增 Chat View ID 清空消息。真正的 ChatSession 由首次发送路径按需创建。
   */
  function startNewChat() {
    if (!selectedId || isLoadingSession) return;
    if (isChatWorking) stop();
    if (activeSession !== null || messages.length > 0) {
      enterDraftChat(selectedId);
    }
    setIsMobileSidebarOpen(false);
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  /** 选择历史会话并从 PostgreSQL 恢复消息，而不是依赖浏览器临时状态。 */
  async function openHistorySession(sessionId: string) {
    if (!sessionId || sessionId === activeSession?.id) return;
    if (isChatWorking) stop();
    setIsSessionOperationPending(true);
    try {
      const detail = await api<ChatSessionDetail>(`/api/chat-sessions/${sessionId}`);
      if (detail.session.knowledge_base_id !== selectedId) {
        throw new Error("历史会话不属于当前知识库");
      }
      setActiveSession(detail.session);
      setInitialSessionMessages(toRAGMessages(detail.messages));
      chatViewSequenceRef.current += 1;
      chatViewKnowledgeBaseRef.current = detail.session.knowledge_base_id;
      setChatViewId(`session:${sessionId}:${chatViewSequenceRef.current}`);
      setPageError("");
      setIsMobileSidebarOpen(false);
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "历史会话加载失败");
    } finally {
      setIsSessionOperationPending(false);
    }
  }

  /**
   * 删除一条知识库会话，并在删除当前会话后恢复一个可继续输入的目标。
   *
   * DELETE 使用知识库父资源路径，前后端共同约束会话归属。删除非当前会话只更新左侧列表；
   * 删除当前会话时优先打开剩余最近会话，没有历史时回到未持久化 Draft，保证 Composer 不会
   * 停在已删除的 session_id，也不会为了占位再次制造空记录。后端会对正在生成的 PENDING
   * 回答返回 409，因此这里不做乐观删除。
   */
  async function deleteSession(sessionId: string) {
    if (!selectedId || deletingSessionId) return;
    if (sessionId === activeSession?.id && isChatWorking) {
      const error = new Error("回答生成完成后才能删除当前会话");
      setPageError(error.message);
      throw error;
    }

    const knowledgeBaseId = selectedId;
    setDeletingSessionId(sessionId);
    try {
      // 只有服务端确认 204 后才从本地列表移除，409/404 时保留原 UI 便于用户重试或核验。
      await api<void>(
        `/api/knowledge-bases/${knowledgeBaseId}/chat-sessions/${sessionId}`,
        { method: "DELETE" },
      );
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "会话删除失败");
      setDeletingSessionId(null);
      throw value;
    }

    const remainingSessions = sessions.filter((session) => session.id !== sessionId);
    setSessions(remainingSessions);
    if (sessionId !== activeSession?.id) {
      setPageError("");
      setDeletingSessionId(null);
      return;
    }

    // 当前消息和证据都绑定旧 session_id。先切到安全的本地 Draft，让 AI SDK 立即销毁旧
    // Chat 实例；若后续历史详情加载失败，用户仍可直接重新提问，而不会看到已删除内容。
    enterDraftChat(knowledgeBaseId);

    try {
      if (remainingSessions.length > 0) {
        const detail = await api<ChatSessionDetail>(
          `/api/chat-sessions/${remainingSessions[0].id}`,
        );
        if (detail.session.knowledge_base_id !== knowledgeBaseId) {
          throw new Error("下一个历史会话不属于当前知识库");
        }
        setActiveSession(detail.session);
        setInitialSessionMessages(toRAGMessages(detail.messages));
        chatViewSequenceRef.current += 1;
        chatViewKnowledgeBaseRef.current = knowledgeBaseId;
        setChatViewId(`session:${detail.session.id}:${chatViewSequenceRef.current}`);
      }
      setPageError("");
    } catch (value) {
      // 会话本身已经成功删除，后续恢复失败不能伪装成“删除失败”；保留 Draft 并说明真实阶段。
      setPageError(
        value instanceof Error
          ? `会话已删除，但无法打开下一会话：${value.message}`
          : "会话已删除，但无法打开下一会话",
      );
    } finally {
      setDeletingSessionId(null);
    }
  }

  /**
   * 刷新一个知识库的历史列表，并按显式 ID 更新仍处于活动状态的会话摘要。
   *
   * 首次发送的函数闭包中 activeSession 仍为 null，因此不能从闭包推断刷新目标。显式传入
   * 本轮创建/使用的 session_id，既能拿到后端生成的新标题，也避免旧流结束后覆盖用户后来
   * 打开的另一会话。若用户已经切换知识库，本次旧响应直接丢弃。
   */
  async function refreshSessions(knowledgeBaseId: string, activeSessionId: string) {
    const values = await api<ChatSession[]>(
      `/api/knowledge-bases/${knowledgeBaseId}/chat-sessions`,
    );
    if (chatViewKnowledgeBaseRef.current !== knowledgeBaseId) return;
    setSessions(values);
    setActiveSession((current) => {
      if (current?.id !== activeSessionId) return current;
      return values.find((session) => session.id === activeSessionId) ?? current;
    });
  }

  /**
   * 根据当前模式发送真实流式问答，或执行不依赖 LLM 的检索调试。
   *
   * Chat 模式在 Draft 首次提交时先持久化会话，再把返回的 session_id 放入流式请求；创建
   * 成功后不会改变 chatViewId，因此当前 AI SDK Chat 实例可以继续接收本轮增量消息。
   * Retrieval 模式只调用检索解释接口，始终不创建 ChatSession。
   */
  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (
      !normalizedQuestion ||
      !selectedId ||
      !hasReadyDocument ||
      isChatWorking ||
      isRetrieving ||
      isLoadingSession ||
      isCreatingSessionRef.current
    ) {
      return;
    }
    setPageError("");
    const knowledgeBaseId = selectedId;
    const retrievalOptions = {
      mode: retrievalMode,
      candidate_k: 30,
      enable_query_rewrite: enableQueryRewrite,
      enable_rerank: enableRerank,
      enable_parent_expansion: enableParentExpansion,
      document_ids: selectedDocumentIds,
    };

    if (mode === "chat") {
      let targetSession = activeSession;

      // Draft 不是业务事实；只有用户真正提交第一条消息时才创建 PostgreSQL 会话。
      // 同步 Ref 比 isLoadingSession 更早生效，用于防止快速双 Enter 产生两条空会话。
      if (!targetSession) {
        isCreatingSessionRef.current = true;
        setIsSessionOperationPending(true);
        try {
          const created = await api<ChatSession>(
            `/api/knowledge-bases/${knowledgeBaseId}/chat-sessions`,
            { method: "POST" },
          );
          targetSession = created;
          setActiveSession(created);
          setSessions((current) => [
            created,
            ...current.filter((session) => session.id !== created.id),
          ]);
        } catch (value) {
          // 创建失败时保留输入内容和 Draft，用户可在外部服务恢复后直接重试。
          setPageError(value instanceof Error ? value.message : "会话创建失败");
          return;
        } finally {
          isCreatingSessionRef.current = false;
          setIsSessionOperationPending(false);
        }
      }

      setQuestion("");
      // 提交后立即把手动扩高的输入框恢复为单行，给回答留出更多可视空间。
      if (composerRef.current) composerRef.current.style.height = "auto";
      try {
        await sendMessage(
          { text: normalizedQuestion },
          {
            body: {
              knowledge_base_id: knowledgeBaseId,
              session_id: targetSession.id,
              question: normalizedQuestion,
              top_k: 5,
              ...retrievalOptions,
            },
          },
        );
        await refreshSessions(knowledgeBaseId, targetSession.id);
      } catch (value) {
        setPageError(value instanceof Error ? value.message : "问答请求失败");
      }
      return;
    }

    setIsRetrieving(true);
    setRetrievalResults([]);
    setRetrievalTrace(null);
    try {
      const response = await api<RetrievalExplainResponse>("/api/retrieval/explain", {
        method: "POST",
        body: JSON.stringify({
          knowledge_base_id: knowledgeBaseId,
          query: normalizedQuestion,
          top_k: 5,
          ...retrievalOptions,
        }),
      });
      setRetrievalResults(response.results);
      setRetrievalTrace(response.trace);
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "检索失败");
    } finally {
      setIsRetrieving(false);
    }
  }

  /** Enter 发送、Shift+Enter 换行，保持常见聊天产品的键盘习惯。 */
  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }

  /**
   * 在用户输入时自动扩展 Composer，但限制在 200px 内，避免长问题挤掉整个消息区域。
   * 高度只属于瞬时视图状态，不需要进入 React State，否则每次按键都会多一次布局渲染。
   */
  function resizeComposer(element: HTMLTextAreaElement) {
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 200)}px`;
  }

  /** 把空状态建议带入输入框并立即聚焦，让用户仍可在发送前编辑问题。 */
  function chooseSuggestion(prompt: string) {
    setQuestion(prompt);
    requestAnimationFrame(() => {
      if (!composerRef.current) return;
      resizeComposer(composerRef.current);
      composerRef.current.focus();
    });
  }

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <ChatSidebar
        knowledgeBase={selectedKnowledgeBase}
        sessions={sessions}
        activeSessionId={activeSession?.id ?? null}
        documentCount={documents.length}
        readyCount={readyCount}
        isLoadingSession={isLoadingSession}
        isChatWorking={isChatWorking}
        deletingSessionId={deletingSessionId}
        isMobileOpen={isMobileSidebarOpen}
        isDesktopCollapsed={isDesktopSidebarCollapsed}
        onCloseMobile={() => setIsMobileSidebarOpen(false)}
        onCollapseDesktop={() => setIsDesktopSidebarCollapsed(true)}
        onExpandDesktop={() => setIsDesktopSidebarCollapsed(false)}
        onCreateSession={startNewChat}
        onOpenSession={(sessionId) => void openHistorySession(sessionId)}
        onDeleteSession={deleteSession}
      />

      <section className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* ChatGPT 式顶栏只保留当前问答范围和二级设置，避免把检索参数长期铺在首屏。 */}
        <header className="relative z-20 flex h-14 shrink-0 items-center justify-between px-3 sm:px-4">
          <div className="flex min-w-0 items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => setIsMobileSidebarOpen(true)}
              aria-label="打开会话侧栏"
              className="md:hidden"
            >
              <Menu />
            </Button>
            {knowledgeBases.length > 0 ? (
              <div className="group relative min-w-0">
                <select
                  value={selectedId ?? ""}
                  onChange={(event) => selectKnowledgeBase(event.target.value)}
                  disabled={deletingSessionId !== null || isLoadingSession}
                  aria-label="选择问答知识库"
                  className="h-10 max-w-[58vw] appearance-none truncate rounded-lg border-0 bg-transparent py-1 pl-2 pr-7 text-base font-semibold outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60 sm:max-w-sm"
                >
                  {knowledgeBases.map((base) => (
                    <option key={base.id} value={base.id}>
                      {base.name}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              </div>
            ) : (
              <span className="px-2 text-base font-semibold">UltimateRAG</span>
            )}
            {selectedKnowledgeBase && !isLoadingDocuments && (
              <span className="hidden text-xs text-muted-foreground lg:inline">
                {readyCount} 份可检索文档
              </span>
            )}
          </div>

          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label="打开检索设置"
            aria-expanded={isSettingsOpen}
            onClick={() => setIsSettingsOpen((open) => !open)}
            className={isSettingsOpen ? "bg-muted" : ""}
          >
            <SlidersHorizontal />
          </Button>

          {isSettingsOpen && (
            <>
              <button
                type="button"
                aria-label="关闭检索设置"
                onClick={() => setIsSettingsOpen(false)}
                className="fixed inset-0 z-20 bg-transparent"
              />
              <aside className="absolute right-3 top-12 z-30 w-[min(24rem,calc(100vw-1.5rem))] overflow-hidden rounded-2xl border border-border bg-popover shadow-2xl">
                <div className="border-b border-border px-4 py-3">
                  <p className="text-sm font-semibold">检索与回答设置</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    高级能力默认启用，需要调试时再调整。
                  </p>
                </div>
                <div className="chat-scrollbar max-h-[70vh] space-y-5 overflow-y-auto p-4 text-sm">
                  <section>
                    <p className="mb-2 text-xs font-medium text-muted-foreground">工作模式</p>
                    <div className="grid grid-cols-2 rounded-xl bg-muted p-1">
                      <button
                        type="button"
                        onClick={() => setMode("chat")}
                        className={
                          mode === "chat"
                            ? "rounded-lg bg-background px-3 py-2 font-medium shadow-sm"
                            : "rounded-lg px-3 py-2 text-muted-foreground"
                        }
                      >
                        RAG 问答
                      </button>
                      <button
                        type="button"
                        onClick={() => setMode("retrieval")}
                        className={
                          mode === "retrieval"
                            ? "rounded-lg bg-background px-3 py-2 font-medium shadow-sm"
                            : "rounded-lg px-3 py-2 text-muted-foreground"
                        }
                      >
                        检索调试
                      </button>
                    </div>
                  </section>

                  <section className="space-y-2">
                    <label htmlFor="retrieval-mode" className="text-xs font-medium text-muted-foreground">
                      检索模式
                    </label>
                    <select
                      id="retrieval-mode"
                      value={retrievalMode}
                      onChange={(event) => setRetrievalMode(event.target.value as RetrievalMode)}
                      className="h-10 w-full rounded-xl border border-border bg-background px-3 outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    >
                      <option value="hybrid">Hybrid · Dense + BM25</option>
                      <option value="dense">Dense · 语义检索</option>
                      <option value="sparse">Sparse · BM25 关键词</option>
                    </select>
                  </section>

                  <section className="space-y-1">
                    {[
                      ["查询改写", enableQueryRewrite, setEnableQueryRewrite],
                      ["Rerank 精排", enableRerank, setEnableRerank],
                      ["Small2Big 上下文扩展", enableParentExpansion, setEnableParentExpansion],
                    ].map(([label, checked, setter]) => (
                      <label
                        key={label as string}
                        className="flex cursor-pointer items-center justify-between rounded-xl px-2 py-2 hover:bg-muted"
                      >
                        <span>{label as string}</span>
                        <input
                          type="checkbox"
                          checked={checked as boolean}
                          onChange={(event) =>
                            (setter as (value: boolean) => void)(event.target.checked)
                          }
                          className="size-4 accent-foreground"
                        />
                      </label>
                    ))}
                  </section>

                  {readyDocuments.length > 0 && (
                    <section>
                      <p className="mb-2 text-xs font-medium text-muted-foreground">
                        文档范围 · {selectedDocumentIds.length === 0 ? "全部" : selectedDocumentIds.length + " 份"}
                      </p>
                      <div className="max-h-44 space-y-0.5 overflow-y-auto rounded-xl border border-border p-1">
                        {readyDocuments.map((document) => (
                          <label
                            key={document.id}
                            className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-2 hover:bg-muted"
                          >
                            <input
                              type="checkbox"
                              checked={selectedDocumentIds.includes(document.id)}
                              onChange={(event) =>
                                setSelectedDocumentIds((current) =>
                                  event.target.checked
                                    ? [...current, document.id]
                                    : current.filter((id) => id !== document.id),
                                )
                              }
                              className="size-4 shrink-0 accent-foreground"
                            />
                            <span className="truncate" title={document.filename}>
                              {document.filename}
                            </span>
                          </label>
                        ))}
                      </div>
                    </section>
                  )}

                  <Link
                    href={selectedId ? "/knowledge-bases/" + selectedId : "/knowledge-bases"}
                    className="flex items-center justify-between rounded-xl border border-border px-3 py-2.5 font-medium hover:bg-muted"
                  >
                    管理当前知识库
                    <FileText className="size-4 text-muted-foreground" />
                  </Link>
                </div>
              </aside>
            </>
          )}
        </header>

        {(pageError || chatError) && (
          <div className="mx-3 mb-1 flex shrink-0 items-start gap-2 rounded-xl bg-destructive/8 px-3 py-2.5 text-sm text-destructive sm:mx-4">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{pageError || chatError?.message}</span>
          </div>
        )}

        {/* 消息流拥有唯一滚动条，顶部栏和 Composer 在长答案期间始终可操作。 */}
        <div className="chat-scrollbar min-h-0 flex-1 overflow-y-auto">
          {mode === "chat" ? (
            <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 sm:px-6">
              {isLoadingKnowledgeBases ? (
                <div className="flex flex-1 items-center justify-center gap-3 text-sm text-muted-foreground">
                  <LoaderCircle className="size-4 animate-spin" /> 正在载入知识空间…
                </div>
              ) : knowledgeBases.length === 0 ? (
                <div className="flex flex-1 flex-col items-center justify-center pb-16 text-center">
                  <span className="grid size-12 place-items-center rounded-full bg-foreground text-background">
                    <BookOpenText className="size-5" />
                  </span>
                  <h1 className="mt-5 text-2xl font-semibold tracking-tight">
                    {pageError ? "暂时无法载入知识库" : "先创建一个知识库"}
                  </h1>
                  <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                    上传企业文档并等待索引完成后，就可以在这里像使用 ChatGPT 一样连续提问。
                  </p>
                  <Button asChild className="mt-5 rounded-full">
                    <Link href="/knowledge-bases">管理知识库</Link>
                  </Button>
                </div>
              ) : messages.length === 0 ? (
                <div className="flex flex-1 flex-col items-center justify-center pb-10 pt-10 text-center">
                  <span className="grid size-12 place-items-center rounded-full bg-foreground text-background shadow-sm">
                    <Sparkles className="size-5" />
                  </span>
                  <h1 className="mt-5 text-balance text-2xl font-semibold tracking-[-0.025em] sm:text-3xl">
                    想从「{selectedKnowledgeBase?.name ?? "知识库"}」了解什么？
                  </h1>
                  <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
                    我会基于已索引文档回答，并把图片、表格和可点击来源放在答案旁边。
                  </p>
                  <div className="mt-8 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
                    {[
                      ["总结核心内容", "概括文档的主题、贡献和关键结论"],
                      ["梳理关键结论", "按重要程度列出有依据的发现"],
                      ["展示图片与表格", "找出文档中的重要视觉信息"],
                      ["核验信息来源", "说明结论分别来自哪些原文位置"],
                    ].map(([title, prompt]) => (
                      <button
                        key={title}
                        type="button"
                        onClick={() => chooseSuggestion(prompt)}
                        disabled={!hasReadyDocument}
                        className="rounded-2xl border border-border px-4 py-3 text-left transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <span className="block text-sm font-medium">{title}</span>
                        <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                          {prompt}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="space-y-9 py-6 sm:py-10">
                  {messages.map((message) => (
                    <RAGMessage key={message.id} message={message} />
                  ))}

                  {chatStatus === "submitted" && (
                    <div className="flex items-center gap-3 text-sm text-muted-foreground">
                      <span className="grid size-7 place-items-center rounded-full bg-foreground text-background">
                        <Bot className="size-3.5" />
                      </span>
                      <span className="flex items-center gap-2">
                        <LoaderCircle className="size-3.5 animate-spin" />
                        正在检索知识并组织回答…
                      </span>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              )}
            </div>
          ) : (
            <div className="mx-auto min-h-full w-full max-w-3xl px-4 py-8 sm:px-6 sm:py-12">
              {retrievalResults.length === 0 && !isRetrieving && !retrievalTrace ? (
                <div className="flex min-h-[50vh] flex-col items-center justify-center text-center">
                  <span className="grid size-12 place-items-center rounded-full bg-foreground text-background">
                    <Search className="size-5" />
                  </span>
                  <h1 className="mt-5 text-2xl font-semibold">检索调试</h1>
                  <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                    输入查询以观察 Dense、BM25、RRF、Rerank 和 Small2Big 的完整结果，不调用生成模型。
                  </p>
                </div>
              ) : isRetrieving ? (
                <div className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-muted-foreground">
                  <LoaderCircle className="size-4 animate-spin" /> 正在执行检索管线…
                </div>
              ) : (
                <RetrievalEvidence results={retrievalResults} trace={retrievalTrace} defaultOpen />
              )}
            </div>
          )}
        </div>

        {/* Composer 采用悬浮胶囊形态；上传动作明确导向知识库，避免误以为聊天附件会绕过摄取。 */}
        <div className="shrink-0 bg-gradient-to-t from-background via-background to-background/0 px-3 pb-2 pt-3 sm:px-6 sm:pb-3">
          <form onSubmit={submitQuestion} className="mx-auto w-full max-w-3xl">
            <div className="rounded-[26px] border border-black/8 bg-[#f4f4f4] p-2 shadow-[0_2px_12px_rgba(0,0,0,0.08)] transition-shadow focus-within:shadow-[0_4px_20px_rgba(0,0,0,0.11)] dark:bg-[#2f2f2f]">
              <Textarea
                ref={composerRef}
                value={question}
                onChange={(event) => {
                  setQuestion(event.target.value);
                  resizeComposer(event.currentTarget);
                }}
                onKeyDown={handleComposerKeyDown}
                rows={1}
                placeholder={
                  knowledgeBases.length === 0
                    ? "请先创建知识库并上传文档"
                    : !selectedId || isLoadingDocuments
                      ? "正在准备问答环境…"
                      : !hasReadyDocument
                        ? "当前知识库没有可检索文档"
                        : mode === "chat"
                          ? "给 UltimateRAG 发消息"
                          : "输入查询以调试检索"
                }
                disabled={!selectedId || !hasReadyDocument || isLoadingSession}
                className="min-h-11 max-h-[200px] resize-none overflow-y-auto border-0 bg-transparent px-3 py-2.5 text-[15px] leading-6 shadow-none focus-visible:ring-0"
              />

              <div className="flex items-center justify-between gap-2 pl-1">
                <div className="flex min-w-0 items-center gap-1">
                  {selectedId ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      asChild
                      className="size-9 rounded-full hover:bg-black/8 dark:hover:bg-white/10"
                    >
                      <Link href={"/knowledge-bases/" + selectedId} aria-label="上传或管理知识库文档">
                        <Plus />
                      </Link>
                    </Button>
                  ) : (
                    <Button type="button" variant="ghost" size="icon" disabled className="size-9 rounded-full">
                      <Plus />
                    </Button>
                  )}
                  <span className="truncate text-xs text-muted-foreground">
                    {mode === "retrieval"
                      ? "检索调试模式"
                      : isLoadingDocuments
                        ? "正在同步文档"
                        : readyCount + " 份文档可用"}
                  </span>
                </div>

                {isChatWorking && mode === "chat" ? (
                  <Button
                    type="button"
                    size="icon"
                    onClick={() => stop()}
                    aria-label="停止生成"
                    className="size-9 rounded-full"
                  >
                    <Square className="size-3 fill-current" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    aria-label={mode === "chat" ? "发送消息" : "执行检索"}
                    disabled={
                      !question.trim() ||
                      !selectedId ||
                      !hasReadyDocument ||
                      isRetrieving ||
                      isLoadingSession
                    }
                    className="size-9 rounded-full"
                  >
                    {mode === "chat" ? <ArrowUp /> : <Search />}
                  </Button>
                )}
              </div>
            </div>
            <p className="mt-2 text-center text-[11px] leading-4 text-muted-foreground">
              UltimateRAG 可能会犯错，请通过答案中的来源核验重要信息。
            </p>
          </form>
        </div>
      </section>
    </div>
  );
}
