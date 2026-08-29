"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import {
  ArrowRight,
  BookOpenText,
  Bot,
  ChevronDown,
  CircleAlert,
  LoaderCircle,
  MessageSquare,
  Search,
  Send,
  Square,
} from "lucide-react";

import {
  api,
  API_URL,
  DocumentItem,
  KnowledgeBase,
  RAGMessage as RAGMessageType,
  RetrievalResult,
} from "@/app/lib";
import { RAGMessage } from "@/components/rag-message";
import { RetrievalEvidence } from "@/components/retrieval-evidence";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type Mode = "chat" | "retrieval";

/**
 * 知识库问答 —— 不依赖 URL 携带知识库 ID 的统一问答页。
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
 *   - useChat 的会话 id 绑定选中的知识库。AI SDK 在 id 变化时会重建会话，
 *     因此切换知识库后消息自动清空，不会把上一个知识库的对话带到新范围。
 *   - RAG 请求体携带 knowledge_base_id，后端按该范围检索，与服务端的知识库
 *     隔离约束保持单一事实来源（URL 不参与范围决定）。
 *
 * 布局结构：
 *   ┌──────────────────────────────────────────────┐
 *   │  RootLayout 顶部导航栏 (64px, 外部提供)       │
 *   ├──────────────────────────────────────────────┤
 *   │  工具栏 (模式切换: RAG 问答 / 检索调试)        │
 *   ├──────────────────────────────────────────────┤
 *   │  消息流 / 检索结果 (独立滚动)                  │
 *   ├──────────────────────────────────────────────┤
 *   │  输入区: [知识库选择器 ▾]  状态摘要            │
 *   │         输入框 (底部固定)                     │
 *   └──────────────────────────────────────────────┘
 *
 *   外层容器使用 h-[calc(100dvh-4rem)] 撑满视口（减去导航栏 64px），
 *   消息区 flex-1 + min-h-0 + overflow-y-auto 独立滚动，输入框始终可见。
 */
export default function ChatPage() {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  // 记录 documents 当前归属的知识库。「已选择但尚未加载完成」由二者差异推导，
  // 避免在 effect 体内同步 setState（React 新规范不推荐，会触发级联渲染）。
  const [loadedForId, setLoadedForId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>([]);
  const [mode, setMode] = useState<Mode>("chat");
  const [pageError, setPageError] = useState("");
  const [isLoadingKnowledgeBases, setIsLoadingKnowledgeBases] = useState(true);
  const [isRetrieving, setIsRetrieving] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Transport 只创建一次。每次提问的 question 放进 sendMessage options，避免闭包保存旧输入，
  // 也让 AI SDK 继续负责取消、请求状态机和 Message Part 的增量合并。
  const transport = useMemo(
    () => new DefaultChatTransport<RAGMessageType>({ api: `${API_URL}/api/chat/stream` }),
    [],
  );

  // 会话 id 绑定知识库：AI SDK 检测到 id 变化会重建 Chat 实例，
  // 这是「切换知识库即清空对话」的实现基础，而不是手动维护消息数组。
  const {
    messages,
    sendMessage,
    status: chatStatus,
    error: chatError,
    stop,
  } = useChat<RAGMessageType>({ id: `knowledge-base-chat-${selectedId ?? "none"}`, transport });

  const isChatWorking = chatStatus === "submitted" || chatStatus === "streaming";
  const hasReadyDocument = documents.some((document) => document.status === "READY");
  const readyCount = documents.filter((document) => document.status === "READY").length;
  const selectedKnowledgeBase = knowledgeBases.find((base) => base.id === selectedId) ?? null;
  // 选中了知识库但文档列表还不属于它，说明正在加载；未选中时无需加载。
  const isLoadingDocuments = selectedId !== null && loadedForId !== selectedId;

  useEffect(() => {
    let isActive = true;
    void api<KnowledgeBase[]>("/api/knowledge-bases")
      .then((values) => {
        if (!isActive) return;
        setKnowledgeBases(values);
        // 默认选中第一个知识库，让页面打开即可提问；切换完全由选择器驱动。
        setSelectedId((current) => current ?? values[0]?.id ?? null);
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
    // 流式阶段每次 Message Part 增长后跟随到底部，让新 token 始终保持可见。
    messagesEndRef.current?.scrollIntoView({
      behavior: chatStatus === "streaming" ? "smooth" : "auto",
    });
  }, [messages, chatStatus]);

  /**
   * 切换问答目标知识库。
   * 消息清空由 useChat 的会话 id 变化自动完成；这里负责清空与旧知识库绑定的
   * 检索结果和错误提示，并在流式进行中时中止旧请求，避免 token 写入已废弃的会话。
   */
  function selectKnowledgeBase(id: string) {
    if (id === selectedId) return;
    if (isChatWorking) stop();
    setSelectedId(id);
    setRetrievalResults([]);
    setPageError("");
  }

  /** 根据当前模式发送真实流式问答，或执行不依赖 LLM 的检索调试。 */
  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || !selectedId || !hasReadyDocument) return;
    setPageError("");

    if (mode === "chat") {
      setQuestion("");
      await sendMessage(
        { text: normalizedQuestion },
        { body: { knowledge_base_id: selectedId, question: normalizedQuestion, top_k: 5 } },
      );
      return;
    }

    setIsRetrieving(true);
    setRetrievalResults([]);
    try {
      const results = await api<RetrievalResult[]>("/api/retrieval/search", {
        method: "POST",
        body: JSON.stringify({ knowledge_base_id: selectedId, query: normalizedQuestion, top_k: 5 }),
      });
      setRetrievalResults(results);
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

  return (
    <div className="flex h-[calc(100dvh-4rem)] flex-col">
      {/* ─── 顶部工具栏：模式切换 ─── */}
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border/70 bg-card/60 px-4 py-2.5 backdrop-blur-sm">
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList>
            <TabsTrigger value="chat">
              <MessageSquare className="size-3.5" /> RAG 问答
            </TabsTrigger>
            <TabsTrigger value="retrieval">
              <Search className="size-3.5" /> 检索调试
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <p className="hidden text-sm text-muted-foreground sm:block">
          {mode === "chat" ? "回答与证据将通过同一条流返回" : "仅执行 Embedding + Milvus Search"}
        </p>
      </div>

      {/* 错误提示 */}
      {(pageError || chatError) && (
        <div className="shrink-0 border-b border-destructive/20 bg-destructive/8 px-4 py-2.5">
          <div className="mx-auto flex max-w-3xl items-start gap-3 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{pageError || chatError?.message}</span>
          </div>
        </div>
      )}

      {/* ─── 消息流 / 检索结果（独立滚动区域） ─── */}
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        {mode === "chat" ? (
          <div className="mx-auto max-w-3xl space-y-6">
            {isLoadingKnowledgeBases ? (
              <div className="flex h-full min-h-[320px] items-center justify-center gap-3 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin text-primary" /> 正在加载知识库…
              </div>
            ) : knowledgeBases.length === 0 ? (
              /* 系统中还没有任何知识库：给出可操作的创建指引，而不是留一个空白聊天框。 */
              <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center">
                <span className="grid size-14 place-items-center rounded-2xl border border-border bg-card shadow-sm">
                  <BookOpenText className="size-6 text-primary" />
                </span>
                <h2 className="mt-5 text-2xl font-semibold tracking-tight">
                  {pageError ? "知识库加载失败" : "还没有可问答的知识库"}
                </h2>
                <p className="mt-3 max-w-md text-base leading-7 text-muted-foreground">
                  {pageError
                    ? "请检查后端服务是否可用，刷新页面重试。"
                    : "先创建一个知识库并上传 Markdown 文档，然后回到这里开始问答。"}
                </p>
                <Button className="mt-6" asChild>
                  <Link href="/knowledge-bases">
                    去创建知识库 <ArrowRight className="size-4" />
                  </Link>
                </Button>
              </div>
            ) : (
              messages.length === 0 && (
                <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center">
                  <span className="grid size-14 place-items-center rounded-2xl bg-foreground text-background shadow-md">
                    <Bot className="size-6" />
                  </span>
                  <h2 className="mt-5 text-2xl font-semibold tracking-tight">从已索引的知识开始</h2>
                  <p className="mt-3 max-w-md text-base leading-7 text-muted-foreground">
                    回答只依据
                    <span className="font-medium text-foreground">
                      「{selectedKnowledgeBase?.name ?? "所选知识库"}」
                    </span>
                    ，并附带命中的文档、章节与相似度，便于核验和学习 RAG 链路。
                  </p>
                  <div className="mt-6 flex flex-wrap justify-center gap-2">
                    {["总结文档的核心内容", "有哪些关键结论？", "依据来自哪些章节？"].map((prompt) => (
                      <button
                        key={prompt}
                        type="button"
                        className="rounded-full border border-border bg-card px-4 py-2 text-sm text-muted-foreground shadow-sm transition-all hover:border-primary/40 hover:text-foreground hover:shadow-md"
                        onClick={() => setQuestion(prompt)}
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              )
            )}

            {/* 消息列表 */}
            {messages.map((message) => (
              <RAGMessage key={message.id} message={message} />
            ))}

            {/* 等待响应指示器 */}
            {chatStatus === "submitted" && (
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <span className="grid size-8 place-items-center rounded-full border border-border bg-foreground text-background">
                  <Bot className="size-4" />
                </span>
                <LoaderCircle className="size-3.5 animate-spin text-primary" />
                正在检索知识并构造上下文…
              </div>
            )}

            {/* 滚动锚点：流式输出时自动滚到此处 */}
            <div ref={messagesEndRef} />
          </div>
        ) : (
          <div className="mx-auto max-w-3xl">
            {retrievalResults.length === 0 && !isRetrieving ? (
              <div className="flex h-full min-h-[320px] flex-col items-center justify-center text-center">
                <span className="grid size-14 place-items-center rounded-2xl border border-border bg-card shadow-sm">
                  <Search className="size-6 text-primary" />
                </span>
                <h2 className="mt-5 text-2xl font-semibold tracking-tight">观察召回，而不调用 LLM</h2>
                <p className="mt-3 max-w-md text-base leading-7 text-muted-foreground">
                  选择知识库后输入查询，查看 Top-5 Chunk、章节路径和余弦相似度，用于单独评估 Retrieval 效果。
                </p>
              </div>
            ) : isRetrieving ? (
              <div className="flex h-full min-h-[320px] items-center justify-center gap-3 text-sm text-muted-foreground">
                <LoaderCircle className="size-4 animate-spin text-primary" /> 正在编码查询并检索向量…
              </div>
            ) : (
              <RetrievalEvidence results={retrievalResults} defaultOpen />
            )}
          </div>
        )}
      </div>

      {/* ─── 底部固定输入区 ─── */}
      <div className="shrink-0 border-t border-border/70 bg-card/80 px-4 py-4 backdrop-blur-sm sm:px-6">
        <form onSubmit={submitQuestion} className="mx-auto max-w-3xl">
          <Card className="gap-0 border-border/80 py-0 shadow-[0_-4px_24px_-12px_oklch(0.22_0.02_60/.15)] focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
            <CardContent className="p-2">
              {/* 知识库选择行：位于输入框上方，先选择问答范围，再输入问题。
                  使用原生 select 而非自制下拉组件：无障碍与键盘行为由浏览器保证，
                  也避免为一个下拉框引入新的第三方依赖。 */}
              {knowledgeBases.length > 0 && (
                <div className="flex min-h-9 flex-wrap items-center gap-x-3 gap-y-1.5 px-2 pb-2 pt-1">
                  <div className="relative">
                    <select
                      value={selectedId ?? ""}
                      onChange={(event) => selectKnowledgeBase(event.target.value)}
                      aria-label="选择进行问答的知识库"
                      className="h-8 appearance-none rounded-lg border border-border bg-background pr-8 pl-3 text-sm font-medium shadow-sm outline-none transition-colors hover:border-primary/40 focus-visible:border-primary/50 focus-visible:ring-2 focus-visible:ring-primary/10"
                    >
                      {knowledgeBases.map((base) => (
                        <option key={base.id} value={base.id}>
                          {base.name}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
                  </div>

                  {/* 文档状态摘要：让「能不能提问」对用户可见，而不是点击发送后才报错。 */}
                  {selectedKnowledgeBase && (
                    <span className="text-sm text-muted-foreground">
                      {isLoadingDocuments
                        ? "正在加载文档状态…"
                        : `${documents.length} 份文档 · ${readyCount} 份可检索`}
                    </span>
                  )}

                  {/* 选中知识库但没有可检索内容时，给出直达文档工作台的修复路径。 */}
                  {selectedId && !isLoadingDocuments && !hasReadyDocument && (
                    <Link
                      href={`/knowledge-bases/${selectedId}`}
                      className="ml-auto inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                    >
                      去上传文档 <ArrowRight className="size-4" />
                    </Link>
                  )}
                </div>
              )}

              <Textarea
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                rows={2}
                placeholder={
                  knowledgeBases.length === 0
                    ? "请先创建知识库并上传文档"
                    : !selectedId || isLoadingDocuments
                      ? "正在准备问答环境…"
                      : !hasReadyDocument
                        ? "当前知识库还没有可检索文档，请先上传"
                        : mode === "chat"
                          ? "询问所选知识库中的内容…"
                          : "输入查询，观察向量召回结果…"
                }
                disabled={!selectedId || !hasReadyDocument}
                className="min-h-[52px] max-h-32 resize-none border-0 bg-transparent px-3 py-2 shadow-none focus-visible:ring-0"
              />
              <div className="flex items-center justify-between gap-3 px-2 pb-1">
                <span className="text-sm text-muted-foreground">Enter 发送 · Shift + Enter 换行</span>
                {isChatWorking && mode === "chat" ? (
                  <Button type="button" size="sm" variant="outline" onClick={() => stop()}>
                    <Square className="size-3 fill-current" /> 停止
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="sm"
                    disabled={!question.trim() || !selectedId || !hasReadyDocument || isRetrieving}
                  >
                    {mode === "chat" ? <Send className="size-3.5" /> : <Search className="size-3.5" />}
                    {mode === "chat" ? "发送" : "检索"}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        </form>
      </div>
    </div>
  );
}
