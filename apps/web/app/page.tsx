"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  BookOpenText,
  Boxes,
  Braces,
  CheckCircle2,
  Database,
  FileSearch,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, KnowledgeBase } from "./lib";

/** 知识库控制台：展示 RAG 产品价值、系统边界与可进入的知识工作区。 */
export default function Home() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setError("");
      setItems(await api<KnowledgeBase[]>("/api/knowledge-bases"));
    } catch (value) {
      setError(value instanceof Error ? value.message : "知识库加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let isActive = true;
    void api<KnowledgeBase[]>("/api/knowledge-bases")
      .then((values) => {
        if (isActive) setItems(values);
      })
      .catch((value: unknown) => {
        if (isActive) setError(value instanceof Error ? value.message : "知识库加载失败");
      })
      .finally(() => {
        if (isActive) setLoading(false);
      });

    // 页面离开后忽略旧请求，避免异步响应继续修改已经卸载的页面状态。
    return () => {
      isActive = false;
    };
  }, []);

  async function create(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");

    try {
      await api<KnowledgeBase>("/api/knowledge-bases", {
        method: "POST",
        body: JSON.stringify({ name, description }),
      });
      setName("");
      setDescription("");
      setDialogOpen(false);
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "知识库创建失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="relative overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-[560px] bg-[radial-gradient(circle_at_20%_0%,oklch(0.92_0.06_70/0.65),transparent_42%),radial-gradient(circle_at_85%_12%,oklch(0.92_0.04_190/0.45),transparent_35%)]" />

      <section className="mx-auto grid max-w-7xl gap-12 px-4 pb-16 pt-16 sm:px-6 md:pt-24 lg:grid-cols-[1.25fr_0.75fr] lg:px-8 lg:pb-24">
        <div className="max-w-3xl">
          <Badge variant="outline" className="mb-6 gap-2 bg-card/70 px-3 py-1.5 backdrop-blur">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-60" />
              <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
            </span>
            检索链路已就绪
          </Badge>
          <h1 className="max-w-3xl text-balance text-5xl font-semibold leading-[1.05] tracking-[-0.045em] sm:text-6xl lg:text-7xl">
            让企业知识成为
            <span className="block text-primary">可验证的智能答案</span>
          </h1>
          <p className="mt-7 max-w-2xl text-pretty text-lg leading-8 text-muted-foreground sm:text-xl">
            从 Markdown 原文、语义切块到 Dense Retrieval 与流式生成，完整保留每一次回答的证据链。
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <Button size="lg" className="gap-2 shadow-lg shadow-primary/15">
                  <Plus />
                  创建知识库
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>创建新的知识库</DialogTitle>
                  <DialogDescription>
                    为一组具有共同业务边界的文档建立独立检索空间。
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={create} className="grid gap-5">
                  <div className="grid gap-2">
                    <label htmlFor="knowledge-base-name" className="text-sm font-medium">
                      名称
                    </label>
                    <Input
                      id="knowledge-base-name"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      maxLength={200}
                      required
                      autoFocus
                      placeholder="例如：RAG 工程手册"
                    />
                  </div>
                  <div className="grid gap-2">
                    <label htmlFor="knowledge-base-description" className="text-sm font-medium">
                      描述
                    </label>
                    <Textarea
                      id="knowledge-base-description"
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      maxLength={2000}
                      placeholder="说明这个知识库包含的内容与使用范围"
                    />
                  </div>
                  <DialogFooter>
                    <Button type="button" variant="ghost" onClick={() => setDialogOpen(false)}>
                      取消
                    </Button>
                    <Button disabled={saving} type="submit">
                      {saving && <LoaderCircle className="animate-spin" />}
                      {saving ? "创建中" : "确认创建"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
            {items[0] && (
              <Button size="lg" variant="outline" asChild>
                <Link href={`/knowledge-bases/${items[0].id}`}>
                  进入最近工作区
                  <ArrowRight />
                </Link>
              </Button>
            )}
          </div>
          <div className="mt-10 flex flex-wrap gap-x-6 gap-y-3 text-sm text-muted-foreground">
            {["来源可追溯", "知识库隔离", "失败状态透明"].map((item) => (
              <span key={item} className="inline-flex items-center gap-2">
                <CheckCircle2 className="size-4 text-emerald-600" />
                {item}
              </span>
            ))}
          </div>
        </div>

        <Card className="relative self-end overflow-hidden border-foreground/10 bg-foreground text-background shadow-2xl shadow-slate-900/15">
          <CardHeader className="border-b border-white/10 pb-5">
            <div className="flex items-center justify-between">
              <Badge className="bg-white/10 text-white hover:bg-white/10">RAG Pipeline</Badge>
              <Braces className="size-5 text-primary" />
            </div>
            <CardTitle className="mt-4 text-xl">一次回答，四个确定性阶段</CardTitle>
            <CardDescription className="text-slate-300">
              检索与生成保持解耦，每一步都可以独立观察和验证。
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {[
              [Search, "Retrieve", "在当前知识库召回 Top-K Chunk"],
              [Boxes, "Build context", "按预算组织带来源编号的证据"],
              [MessageSquareText, "Generate", "仅依据受控知识上下文流式回答"],
              [ShieldCheck, "Cite", "由稳定文档与 Chunk 元数据生成引用"],
            ].map(([Icon, title, copy], index) => {
              const PipelineIcon = Icon as typeof Search;
              return (
                <div key={title as string} className="flex items-start gap-4 rounded-xl border border-white/10 bg-white/[0.04] p-4">
                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/20 text-primary">
                    <PipelineIcon className="size-4" />
                  </span>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-slate-500">0{index + 1}</span>
                      <p className="text-sm font-medium text-white">{title as string}</p>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-slate-400">{copy as string}</p>
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      </section>

      <section className="mx-auto max-w-7xl px-4 pb-24 sm:px-6 lg:px-8">
        {error && (
          <div className="mb-6 rounded-xl border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="mb-7 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
          <div>
            <p className="text-sm font-medium text-primary">Knowledge spaces</p>
            <h2 className="mt-2 text-3xl font-semibold tracking-tight">你的知识库</h2>
            <p className="mt-2 text-sm text-muted-foreground">选择一个空间管理文档、调试检索并开始对话。</p>
          </div>
          <Badge variant="secondary" className="px-3 py-1.5">
            {loading ? "同步中" : `${items.length} 个空间`}
          </Badge>
        </div>

        {loading ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((item) => (
              <div key={item} className="h-48 animate-pulse rounded-2xl border bg-card/60" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <Card className="border-dashed py-16 text-center">
            <CardContent className="mx-auto max-w-md">
              <span className="mx-auto grid size-12 place-items-center rounded-2xl bg-primary/10 text-primary">
                <BookOpenText />
              </span>
              <h3 className="mt-5 text-lg font-semibold">从第一个知识库开始</h3>
              <p className="mt-2 text-sm leading-6 text-muted-foreground">
                创建空间并上传 Markdown，系统会自动完成解析、切块、向量化和索引。
              </p>
              <Button className="mt-6" onClick={() => setDialogOpen(true)}>
                <Plus />
                创建知识库
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {items.map((item, index) => (
              <Link key={item.id} href={`/knowledge-bases/${item.id}`} className="group outline-none">
                <Card className="h-full gap-5 transition duration-200 group-hover:-translate-y-1 group-hover:border-primary/30 group-hover:shadow-xl group-focus-visible:ring-2 group-focus-visible:ring-ring">
                  <CardHeader>
                    <div className="mb-5 flex items-center justify-between">
                      <span className="grid size-10 place-items-center rounded-xl bg-accent text-accent-foreground">
                        <Database className="size-4.5" />
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">KB-{String(index + 1).padStart(2, "0")}</span>
                    </div>
                    <CardTitle className="text-lg">{item.name}</CardTitle>
                    <CardDescription className="line-clamp-2 min-h-11">
                      {item.description || "尚未添加知识库描述"}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="mt-auto flex items-center justify-between border-t pt-5 text-xs text-muted-foreground">
                    <span>创建于 {new Date(item.created_at).toLocaleDateString("zh-CN")}</span>
                    <span className="inline-flex items-center gap-1 font-medium text-foreground">
                      打开工作区
                      <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-1" />
                    </span>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </section>

      <section id="architecture" className="border-y bg-card/60">
        <div className="mx-auto grid max-w-7xl gap-4 px-4 py-14 sm:px-6 md:grid-cols-3 lg:px-8">
          {[
            [Database, "PostgreSQL", "业务事实、文档状态与 Chunk 元数据"],
            [FileSearch, "MinIO", "可排查、可重建的原始文档事实"],
            [Sparkles, "Milvus", "按知识库隔离的可重建向量索引"],
          ].map(([Icon, title, copy]) => {
            const ArchitectureIcon = Icon as typeof Database;
            return (
              <div key={title as string} className="flex gap-4 rounded-2xl p-5">
                <ArchitectureIcon className="mt-1 size-5 shrink-0 text-primary" />
                <div>
                  <h3 className="font-medium">{title as string}</h3>
                  <p className="mt-1 text-sm leading-6 text-muted-foreground">{copy as string}</p>
                </div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
