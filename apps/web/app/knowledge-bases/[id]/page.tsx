"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  FileText,
  LoaderCircle,
  Trash2,
  Upload,
} from "lucide-react";

import { api, DocumentItem, KnowledgeBase } from "@/app/lib";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";

const STATUS_PRESENTATION: Record<string, { label: string; className: string }> = {
  READY: { label: "可检索", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  FAILED: { label: "失败", className: "border-red-200 bg-red-50 text-red-700" },
  PENDING: { label: "排队中", className: "border-slate-200 bg-slate-50 text-slate-600" },
  PARSING: { label: "解析中", className: "border-amber-200 bg-amber-50 text-amber-700" },
  CHUNKING: { label: "切分中", className: "border-amber-200 bg-amber-50 text-amber-700" },
  EMBEDDING: { label: "向量化", className: "border-amber-200 bg-amber-50 text-amber-700" },
  INDEXING: { label: "索引中", className: "border-amber-200 bg-amber-50 text-amber-700" },
};

const TERMINAL_DOCUMENT_STATUSES = new Set(["READY", "FAILED"]);

/**
 * 知识库工作台 —— 管理单个知识库的文档事实。
 *
 * 职责边界：
 *   本页面只负责知识库的文档管理：上传、查看处理状态、删除。
 *   问答与检索调试已迁移到统一的 /chat 页面，由页面内的知识库选择器决定范围，
 *   这里通过「进入知识问答」入口跳转。
 *
 * 设计背景：
 *   文档管理强绑定一个具体知识库（上传目标、删除对象都需要明确 ID），保留
 *   /knowledge-bases/[id] 路由是合理的；而问答需要在多个知识库之间自由切换，
 *   由 URL 决定范围会迫使客户在知识库之间反复跳转。两者按职责拆分后，
 *   每个页面的状态和交互都更简单。
 */
export default function KnowledgeBaseWorkspacePage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [pageError, setPageError] = useState("");
  const [isUploading, setIsUploading] = useState(false);

  const readyCount = documents.filter((document) => document.status === "READY").length;
  const hasProcessingDocuments = documents.some(
    (document) => !TERMINAL_DOCUMENT_STATUSES.has(document.status),
  );

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

  /**
   * 只在存在非终态文档时轮询。页面离开或全部完成后立即清除定时器，避免空知识库也持续
   * 请求；两秒间隔兼顾状态可见性与数据库读取压力，不要求用户手动刷新页面。
   */
  useEffect(() => {
    if (!hasProcessingDocuments) return;
    let isActive = true;
    const timer = window.setInterval(() => {
      void api<DocumentItem[]>(`/api/knowledge-bases/${id}/documents`)
        .then((docs) => {
          if (isActive) setDocuments(docs);
        })
        .catch((value: unknown) => {
          if (isActive) setPageError(value instanceof Error ? value.message : "文档状态刷新失败");
        });
    }, 2000);
    return () => {
      isActive = false;
      window.clearInterval(timer);
    };
  }, [hasProcessingDocuments, id]);

  /** 上传只等待可靠保存和任务入队；解析、向量化和索引由后台 Worker 继续执行。 */
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.namedItem("document") as HTMLInputElement;
    if (!input.files?.[0]) return;

    setIsUploading(true);
    setPageError("");
    const data = new FormData();
    data.append("file", input.files[0]);
    try {
      const accepted = await api<DocumentItem>(`/api/knowledge-bases/${id}/documents`, {
        method: "POST",
        body: data,
      });
      // 202 响应已经包含 PENDING 文档，直接加入列表即可启动上方轮询，无需等待一次额外 GET。
      setDocuments((current) => [accepted, ...current.filter((item) => item.id !== accepted.id)]);
      form.reset();
    } catch (value) {
      setPageError(value instanceof Error ? value.message : "文档上传失败");
    } finally {
      setIsUploading(false);
    }
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
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6">
      {/* ─── 头部：返回入口 + 知识库信息 + 问答入口 ─── */}
      <Link
        href="/knowledge-bases"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        所有知识库
      </Link>

      <header className="mt-5 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {knowledgeBase?.name ?? "载入中…"}
            </h1>
            <Badge className="shrink-0 border-emerald-200 bg-emerald-50 text-sm text-emerald-700 shadow-none hover:bg-emerald-50">
              <span className="size-1 rounded-full bg-emerald-500" />
              V3
            </Badge>
          </div>
          <p className="mt-2 max-w-2xl text-base leading-7 text-muted-foreground">
            {knowledgeBase?.description || "上传可信文档，获得可追溯的回答。"}
          </p>
          <div className="mt-3 flex items-center gap-4 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <FileText className="size-4" /> {documents.length} 份文档
            </span>
            <span className="flex items-center gap-1.5">
              <CheckCircle2 className="size-4 text-emerald-600" /> {readyCount} 份可检索
            </span>
          </div>
        </div>

        <Button asChild className="shrink-0">
          <Link href={`/chat?knowledge_base_id=${id}`}>
            进入知识问答
            <ArrowRight />
          </Link>
        </Button>
      </header>

      {/* 错误提示 */}
      {pageError && (
        <div className="mt-6 rounded-xl border border-destructive/20 bg-destructive/8 px-4 py-3">
          <div className="flex items-start gap-3 text-sm text-destructive">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{pageError}</span>
          </div>
        </div>
      )}

      <Separator className="mt-8" />

      {/* ─── 知识来源：上传 + 文档列表 ─── */}
      <section className="mt-8">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold tracking-wider text-muted-foreground uppercase">知识来源</h2>
          <Badge variant="secondary" className="rounded-md font-mono text-sm">
            {documents.length}
          </Badge>
        </div>

        <form onSubmit={upload}>
          <label className="group flex cursor-pointer items-center gap-4 rounded-2xl border border-dashed border-border bg-card/60 px-5 py-6 transition-colors hover:border-primary/50 hover:bg-primary/5">
            {isUploading ? (
              <LoaderCircle className="size-5 shrink-0 animate-spin text-primary" />
            ) : (
              <Upload className="size-5 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
            )}
            <div className="min-w-0">
              <span className="block text-base font-medium">
                {isUploading ? "正在上传并入队…" : "上传文档"}
              </span>
              <span className="mt-1 block text-sm text-muted-foreground">
                上传成功立即返回，后台解析完成后状态会自动更新 · 最大 10 MB
              </span>
            </div>
            <input
              className="sr-only"
              type="file"
              name="document"
              accept=".md,.markdown,.pdf,.docx,.xlsx,.pptx,.html,.htm,.png,.jpg,.jpeg,.webp,.tif,.tiff,.bmp"
              disabled={isUploading}
              onChange={(event) => event.currentTarget.form?.requestSubmit()}
            />
          </label>
        </form>

        {documents.length === 0 ? (
          <div className="mt-4 rounded-2xl border border-border/60 bg-background/50 px-6 py-12 text-center">
            <FileText className="mx-auto size-6 text-muted-foreground/60" />
            <p className="mt-4 text-lg font-medium">还没有知识来源</p>
            <p className="mt-2 text-base leading-7 text-muted-foreground">
              上传第一份文档后，即可在「知识问答」中就这些内容提问。
            </p>
          </div>
        ) : (
          <div className="mt-4 space-y-2">
            {documents.map((document) => {
              const presentation = STATUS_PRESENTATION[document.status] ?? {
                label: document.status,
                className: "",
              };
              const canDelete = TERMINAL_DOCUMENT_STATUSES.has(document.status);
              return (
                <article
                  key={document.id}
                  className="group flex items-start gap-4 rounded-2xl border border-border/60 bg-card/60 px-4 py-3.5 transition-colors hover:border-border hover:bg-card"
                >
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-border/60 bg-background">
                    <FileText className="size-4.5 text-muted-foreground" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-base font-medium" title={document.filename}>
                      {document.filename}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-2.5">
                      <Badge variant="outline" className={`text-sm ${presentation.className}`}>
                        {presentation.label}
                      </Badge>
                      {document.parser_name && (
                        <Badge variant="secondary" className="rounded-md font-mono text-sm">
                          {document.parser_name}
                          {document.parser_version ? `@${document.parser_version}` : ""}
                        </Badge>
                      )}
                      <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
                        <Clock3 className="size-4" />
                        {new Date(document.created_at).toLocaleString("zh-CN")}
                      </span>
                    </div>
                    {document.error_message && (
                      <p className="mt-2 line-clamp-2 text-sm leading-6 text-destructive">
                        {document.error_message}
                      </p>
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    className="size-8 shrink-0 text-muted-foreground opacity-0 hover:text-destructive group-hover:opacity-100 focus:opacity-100"
                    onClick={() => removeDocument(document.id)}
                    disabled={!canDelete}
                    title={canDelete ? "删除文档" : "后台处理完成后才能删除"}
                    aria-label={`删除 ${document.filename}`}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
