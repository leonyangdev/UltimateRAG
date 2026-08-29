"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ArrowRight,
  BookOpenText,
  Database,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Trash2,
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
import { api, KnowledgeBase } from "@/app/lib";

/**
 * 知识库列表页 —— 独立的 /knowledge-bases 路由。
 *
 * 职责边界：
 *   负责知识库的枚举与创建。列表中的卡片进入对应的文档工作台
 *   （/knowledge-bases/[id]）；问答本身在统一的 /chat 页面完成。
 *
 * 设计背景：
 *   此前知识库列表放在首页第二屏，导致首页同时承担「项目介绍」和「工作入口」
 *   两个职责。拆分后首页只做介绍，这里成为管理知识库的唯一入口。
 */
export default function KnowledgeBasesPage() {
  const [items, setItems] = useState<KnowledgeBase[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
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

  /**
   * 删除知识库及其文档、原文件和派生向量。
   *
   * V1 后端执行同步尽力删除，只有三类存储均清理成功才返回 204。前端因此等待请求完成后
   * 再刷新列表，不做乐观删除，避免外部存储失败时界面提前隐藏仍可用于补偿的事实记录。
   */
  async function removeKnowledgeBase(item: KnowledgeBase) {
    const confirmed = window.confirm(
      `确认删除知识库“${item.name}”及其中全部文档和向量索引？此操作不可撤销。`,
    );
    if (!confirmed) return;

    setDeletingId(item.id);
    setError("");
    try {
      await api<void>(`/api/knowledge-bases/${item.id}`, { method: "DELETE" });
      await load();
    } catch (value) {
      setError(value instanceof Error ? value.message : "知识库删除失败");
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-10 sm:px-6 lg:px-8">
      {/* ─── 页头：标题 + 统计 + 入口动作 ─── */}
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
        <div>
          <p className="text-sm font-medium text-primary">Knowledge spaces</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight">你的知识库</h1>
          <p className="mt-2 text-base text-muted-foreground">
            选择一个空间管理文档，或直接进入统一问答页开始对话。
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant="secondary" className="px-3 py-1.5">
            {loading ? "同步中" : `${items.length} 个空间`}
          </Badge>
          <Button variant="outline" asChild>
            <Link href="/chat">
              <MessageSquareText />
              开始问答
            </Link>
          </Button>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button className="gap-2 shadow-lg shadow-primary/15">
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
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mt-8 rounded-xl border border-destructive/20 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {/* ─── 知识库卡片 ─── */}
      <section className="mt-8">
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
              <p className="mt-2 text-base leading-7 text-muted-foreground">
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
              <Card
                key={item.id}
                className="group h-full gap-5 transition duration-200 hover:-translate-y-1 hover:border-primary/30 hover:shadow-xl"
              >
                <CardHeader>
                  <div className="mb-5 flex items-center justify-between">
                    <span className="grid size-10 place-items-center rounded-xl bg-accent text-accent-foreground">
                      <Database className="size-4.5" />
                    </span>
                    <div className="flex items-center gap-1">
                      <span className="font-mono text-sm text-muted-foreground">
                        KB-{String(index + 1).padStart(2, "0")}
                      </span>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        disabled={deletingId !== null}
                        aria-label={`删除知识库 ${item.name}`}
                        onClick={() => void removeKnowledgeBase(item)}
                      >
                        {deletingId === item.id ? (
                          <LoaderCircle className="animate-spin" />
                        ) : (
                          <Trash2 />
                        )}
                      </Button>
                    </div>
                  </div>
                  <CardTitle className="text-lg">{item.name}</CardTitle>
                  <CardDescription className="line-clamp-2 min-h-11">
                    {item.description || "尚未添加知识库描述"}
                  </CardDescription>
                </CardHeader>
                <CardContent className="mt-auto flex items-center justify-between border-t pt-5 text-sm text-muted-foreground">
                  <span>创建于 {new Date(item.created_at).toLocaleDateString("zh-CN")}</span>
                  <Button variant="link" className="h-auto gap-1 p-0 text-foreground" asChild>
                    <Link href={`/knowledge-bases/${item.id}`}>
                      打开工作区
                      <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-1" />
                    </Link>
                  </Button>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
