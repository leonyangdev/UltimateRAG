"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import {
  ArrowLeft,
  Bot,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  LoaderCircle,
  MessageSquare,
  Search,
  Send,
  Sparkles,
  Square,
  Trash2,
  Upload,
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

type Mode = "chat" | "retrieval";

const STATUS_PRESENTATION: Record<string, { label: string; className: string }> = {
  READY: { label: "可检索", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  FAILED: { label: "失败", className: "border-red-200 bg-red-50 text-red-700" },
  PENDING: { label: "等待", className: "border-slate-200 bg-slate-50 text-slate-600" },
  PARSING: { label: "解析中", className: "border-amber-200 bg-amber-50 text-amber-700" },
  CHUNKING: { label: "切分中", className: "border-amber-200 bg-amber-50 text-amber-700" },
  EMBEDDING: { label: "向量化", className: "border-amber-200 bg-amber-50 text-amber-700" },
  INDEXING: { label: "索引中", className: "border-amber-200 bg-amber-50 text-amber-700" },
};

/**
 * 单知识库的工作台入口。
 * 左侧维护可检索事实，右侧在“RAG 问答”和“检索调试”之间切换，让学习者既能使用答案，
 * 也能直接观察答案背后的 Dense Retrieval 结果和相似度分数。
 */
export default function KnowledgeBasePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [question, setQuestion] = useState("");
  const [retrievalResults, setRetrievalResults] = useState<RetrievalResult[]>([]);
  const [mode, setMode] = useState<Mode>("chat");
  const [pageError, setPageError] = useState("");
  const [isDocumentWorking, setIsDocumentWorking] = useState(false);
  const [isRetrieving, setIsRetrieving] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Transport 只创建一次。每次提问的 question 放进 sendMessage options，避免闭包保存旧输入，
  // 也让 AI SDK 继续负责取消、请求状态机和 Message Part 的增量合并。
  const transport = useMemo(
    () => new DefaultChatTransport<RAGMessageType>({ api: `${API_URL}/api/chat/stream` }),
    [],
  );
  const {
    messages,
    sendMessage,
    status: chatStatus,
    error: chatError,
    stop,
  } = useChat<RAGMessageType>({ id: `knowledge-base-${id}`, transport });

  const isChatWorking = chatStatus === "submitted" || chatStatus === "streaming";
  const hasReadyDocument = documents.some((document) => document.status === "READY");

  /**
   * 并行刷新知识库元数据与文档状态。
   * 两个读取相互独立，并行执行可减少进入工作台时等待的网络往返时间。
   */
  const load = useCallback(async () => {
    try {
      const [kb, docs] = await Promise.all([
        api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
        api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
      ]);
      setKnowledgeBase(kb);
      setDocuments(docs);
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "工作台加载失败");
    }
  }, [id]);

  useEffect(() => {
    let isActive = true;
    void Promise.all([
      api<KnowledgeBase>(`/api/knowledge-bases/${id}`),
      api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`),
    ])
      .then(([kb, docs]) => {
        if (!isActive) return;
        setKnowledgeBase(kb);
        setDocuments(docs);
      })
      .catch((value: unknown) => {
        if (isActive) setPageError(value instanceof Error ? value.message : "工作台加载失败");
      });

    // 知识库路由切换时忽略旧请求，防止上一知识库的数据覆盖当前页面。
    return () => {
      isActive = false;
    };
  }, [id]);

  useEffect(() => {
    // 流式阶段每次 Message Part 增长后跟随到底部，让新 token 始终保持可见。
    // smooth 只影响滚动位置，不参与文本生成，因此不会制造“假流式”效果。
    messagesEndRef.current?.scrollIntoView({
      behavior: chatStatus === "streaming" ? "smooth" : "auto",
    });
  }, [messages, chatStatus]);

  /** 上传 Markdown；同步 API 返回时文档已经完成 Parse → Chunk → Embed → Index。 */
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("document") as HTMLInputElement;
    if (!input.files?.[0]) return;

    setIsDocumentWorking(true);
    setPageError("");
    const data = new FormData();
    data.append("file", input.files[0]);
    try {
      await api<DocumentItem>(`/api/knowledge-bases/${id}/documents`, {
        method: "POST",
        body: data,
      });
      form.reset();
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "文档上传失败");
    } finally {
      await load();
      setIsDocumentWorking(false);
    }
  }

  /** 根据当前模式发送真实流式问答，或执行不依赖 LLM 的检索调试。 */
  async function submitQuestion(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || !hasReadyDocument) return;
    setPageError("");

    if (mode === "chat") {
      setQuestion("");
      await sendMessage(
        { text: normalizedQuestion },
        { body: { knowledge_base_id: id, question: normalizedQuestion, top_k: 5 } },
      );
      return;
    }

    setIsRetrieving(true);
    setRetrievalResults([]);
    try {
      const results = await api<RetrievalResult[]>("/api/retrieval/search", {
        method: "POST",
        body: JSON.stringify({ knowledge_base_id: id, query: normalizedQuestion, top_k: 5 }),
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

  /** 用户确认后同步清理文档事实、原文件和派生向量。 */
  async function removeDocument(documentId: string) {
    if (!window.confirm("确认删除这份文档及其向量索引？此操作不可撤销。")) return;
    setPageError("");
    try {
      await api<void>(`/api/documents/${documentId}`, { method: "DELETE" });
      await load();
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "文档删除失败");
    }
  }

  return (
    <div className="min-h-[calc(100vh-4rem)] bg-[radial-gradient(circle_at_12%_0%,oklch(0.92_0.035_78/.5),transparent_32%)]">
      <div className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
        <Link
          href="/"
          className="mb-5 inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
          所有知识库
        </Link>

        <header className="mb-6 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="gap-1.5 bg-card/80 text-[10px] tracking-[0.16em]">
                <Sparkles className="size-3 text-primary" />
                KNOWLEDGE WORKBENCH
              </Badge>
              <Badge className="border-emerald-200 bg-emerald-50 text-emerald-700 shadow-none hover:bg-emerald-50">
                <span className="size-1.5 rounded-full bg-emerald-500" />
                V1 在线
              </Badge>
            </div>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              {knowledgeBase?.name ?? "正在载入知识库…"}
            </h1>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              {knowledgeBase?.description || "上传可信文档，观察检索证据，并获得可追溯的回答。"}
            </p>
          </div>
          <div className="flex items-center gap-5 text-xs text-muted-foreground">
            <span className="flex items-center gap-2">
              <FileText className="size-4" /> {documents.length} 份文档
            </span>
            <span className="flex items-center gap-2">
              <CheckCircle2 className="size-4 text-emerald-600" />
              {documents.filter((document) => document.status === "READY").length} 份可检索
            </span>
          </div>
        </header>

        {(pageError || chatError) && (
          <div className="mb-4 flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/8 px-4 py-3 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{pageError || chatError?.message}</span>
          </div>
        )}

        <div className="grid min-h-[680px] overflow-hidden rounded-2xl border border-border/70 bg-card/75 shadow-[0_24px_80px_-48px_oklch(0.25_0.02_60/.45)] backdrop-blur lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="border-b border-border/70 bg-muted/20 lg:border-r lg:border-b-0">
            <div className="flex items-center justify-between px-5 py-4">
              <div>
                <h2 className="text-sm font-semibold">知识来源</h2>
                <p className="mt-1 text-xs text-muted-foreground">事实存储与索引状态</p>
              </div>
              <Badge variant="secondary" className="rounded-md font-mono text-[10px]">
                {documents.length}
              </Badge>
            </div>
            <Separator />

            <form onSubmit={upload} className="p-4">
              <label className="group flex cursor-pointer flex-col items-center rounded-xl border border-dashed border-border bg-background/70 px-4 py-5 text-center transition-colors hover:border-primary/50 hover:bg-primary/5">
                {isDocumentWorking ? (
                  <LoaderCircle className="size-5 animate-spin text-primary" />
                ) : (
                  <Upload className="size-5 text-muted-foreground transition-colors group-hover:text-primary" />
                )}
                <span className="mt-2 text-xs font-medium">
                  {isDocumentWorking ? "正在处理文档…" : "选择 Markdown 文档"}
                </span>
                <span className="mt-1 text-[11px] text-muted-foreground">UTF-8 · 最大 10 MB</span>
                <input
                  className="sr-only"
                  type="file"
                  name="document"
                  accept=".md,.markdown,text/markdown"
                  disabled={isDocumentWorking}
                  onChange={(event) => event.currentTarget.form?.requestSubmit()}
                />
              </label>
            </form>

            <div className="max-h-[470px] space-y-2 overflow-y-auto px-3 pb-4 lg:max-h-[calc(100vh-23rem)]">
              {documents.length === 0 && (
                <div className="mx-2 rounded-xl border border-border/60 bg-background/50 px-4 py-8 text-center">
                  <FileText className="mx-auto size-5 text-muted-foreground/60" />
                  <p className="mt-3 text-xs font-medium">还没有知识来源</p>
                  <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                    上传第一份 Markdown 后即可开始问答。
                  </p>
                </div>
              )}

              {documents.map((document) => {
                const presentation = STATUS_PRESENTATION[document.status] ?? {
                  label: document.status,
                  className: "",
                };
                return (
                  <article
                    key={document.id}
                    className="group rounded-xl border border-transparent px-3 py-3 transition-colors hover:border-border/70 hover:bg-background/75"
                  >
                    <div className="flex items-start gap-3">
                      <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-border/60 bg-card">
                        <FileText className="size-4 text-muted-foreground" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium" title={document.filename}>
                          {document.filename}
                        </p>
                        <div className="mt-1.5 flex items-center gap-2">
                          <Badge variant="outline" className={`h-5 px-1.5 text-[9px] ${presentation.className}`}>
                            {presentation.label}
                          </Badge>
                          <span className="flex items-center gap-1 text-[10px] text-muted-foreground">
                            <Clock3 className="size-2.5" />
                            {new Date(document.created_at).toLocaleDateString("zh-CN")}
                          </span>
                        </div>
                        {document.error_message && (
                          <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-destructive">
                            {document.error_message}
                          </p>
                        )}
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="size-7 shrink-0 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100 focus:opacity-100"
                        onClick={() => removeDocument(document.id)}
                        aria-label={`删除 ${document.filename}`}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </article>
                );
              })}
            </div>
          </aside>

          <section className="flex min-h-[680px] min-w-0 flex-col bg-background/45">
            <div className="flex flex-col justify-between gap-3 border-b border-border/70 px-4 py-3 sm:flex-row sm:items-center sm:px-6">
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
              <p className="text-[11px] text-muted-foreground">
                {mode === "chat" ? "回答与证据将通过同一条流返回" : "仅执行 Embedding + Milvus Search"}
              </p>
            </div>

            <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
              {mode === "chat" ? (
                <div className="mx-auto max-w-3xl space-y-6">
                  {messages.length === 0 && (
                    <div className="flex min-h-[390px] flex-col items-center justify-center text-center">
                      <span className="grid size-12 place-items-center rounded-2xl bg-foreground text-background shadow-sm">
                        <Bot className="size-5" />
                      </span>
                      <h2 className="mt-5 text-xl font-semibold tracking-tight">从已索引的知识开始</h2>
                      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                        回答只依据当前知识库，并附带命中的文档、章节与相似度，便于核验和学习 RAG 链路。
                      </p>
                      <div className="mt-6 flex flex-wrap justify-center gap-2">
                        {["总结文档的核心内容", "有哪些关键结论？", "依据来自哪些章节？"].map((prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            className="rounded-full border border-border bg-card px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
                            onClick={() => setQuestion(prompt)}
                          >
                            {prompt}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  {messages.map((message) => (
                    <RAGMessage key={message.id} message={message} />
                  ))}

                  {chatStatus === "submitted" && (
                    <div className="flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="grid size-8 place-items-center rounded-full border border-border bg-foreground text-background">
                        <Bot className="size-4" />
                      </span>
                      <LoaderCircle className="size-3.5 animate-spin text-primary" />
                      正在检索知识并构造上下文…
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>
              ) : (
                <div className="mx-auto max-w-3xl">
                  {retrievalResults.length === 0 && !isRetrieving ? (
                    <div className="flex min-h-[390px] flex-col items-center justify-center text-center">
                      <span className="grid size-12 place-items-center rounded-2xl border border-border bg-card">
                        <Search className="size-5 text-primary" />
                      </span>
                      <h2 className="mt-5 text-xl font-semibold tracking-tight">观察召回，而不调用 LLM</h2>
                      <p className="mt-2 max-w-md text-sm leading-6 text-muted-foreground">
                        输入查询后查看 Top-5 Chunk、章节路径和余弦相似度，用于单独评估 Retrieval 效果。
                      </p>
                    </div>
                  ) : isRetrieving ? (
                    <div className="flex min-h-[390px] items-center justify-center gap-3 text-sm text-muted-foreground">
                      <LoaderCircle className="size-4 animate-spin text-primary" /> 正在编码查询并检索向量…
                    </div>
                  ) : (
                    <RetrievalEvidence results={retrievalResults} defaultOpen />
                  )}
                </div>
              )}
            </div>

            <div className="border-t border-border/70 bg-card/80 p-4 sm:px-6 sm:py-5">
              <form onSubmit={submitQuestion} className="mx-auto max-w-3xl">
                <Card className="gap-0 border-border/80 py-0 shadow-[0_14px_36px_-24px_oklch(0.22_0.02_60/.5)] focus-within:border-primary/50 focus-within:ring-2 focus-within:ring-primary/10">
                  <CardContent className="p-2">
                    <Textarea
                      value={question}
                      onChange={(event) => setQuestion(event.target.value)}
                      onKeyDown={handleComposerKeyDown}
                      rows={2}
                      placeholder={
                        hasReadyDocument
                          ? mode === "chat"
                            ? "询问当前知识库中的内容…"
                            : "输入查询，观察向量召回结果…"
                          : "请先上传并成功索引一份文档"
                      }
                      disabled={!hasReadyDocument}
                      className="min-h-20 resize-none border-0 bg-transparent px-3 py-2 shadow-none focus-visible:ring-0"
                    />
                    <div className="flex items-center justify-between gap-3 px-2 pb-1">
                      <span className="text-[10px] text-muted-foreground">Enter 发送 · Shift + Enter 换行</span>
                      {isChatWorking && mode === "chat" ? (
                        <Button type="button" size="sm" variant="outline" onClick={() => stop()}>
                          <Square className="size-3 fill-current" /> 停止
                        </Button>
                      ) : (
                        <Button
                          type="submit"
                          size="sm"
                          disabled={!question.trim() || !hasReadyDocument || isRetrieving}
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
          </section>
        </div>
      </div>
    </div>
  );
}
