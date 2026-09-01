"use client";

/* 来源预览由受控 API 动态返回，原始尺寸未知。 */
/* eslint-disable @next/next/no-img-element */

import { BookOpen, ImageIcon, Table2, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  API_URL,
  formatLocator,
  type Citation,
  type RetrievalResult,
} from "@/app/lib";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface SourceSidebarProps {
  sourceNumber: number | null;
  citations: Citation[];
  results: RetrievalResult[];
  onClose: () => void;
}

/** 侧栏已单独展示白名单 Asset，因此正文只保留图片标题、解读和其他结构化文本。 */
function withoutAssetMarkers(content: string): string {
  return content.replace(/!\[[^\]]*\]\(asset:\/\/[^)]+\)\s*/g, "");
}

/**
 * 在视口右侧展示某个答案来源的可核验证据。
 *
 * 侧栏严格按后端 Citation 顺序解释 ``[来源 N]``，再用 Chunk ID 关联 RetrievalResult；
 * 不从模型答案反向解析文件名、页码或图片地址，因此自由文本无法伪造来源详情。
 * Radix Dialog 负责 Escape 关闭、焦点陷阱和关闭后的焦点恢复；视觉上把标准居中 Dialog
 * 定位为右侧抽屉，避免手写遮罩遗漏键盘与屏幕阅读器行为。
 */
export function SourceSidebar({ sourceNumber, citations, results, onClose }: SourceSidebarProps) {
  const citation = sourceNumber === null ? undefined : citations[sourceNumber - 1];
  // 只按 Citation 的稳定 Chunk ID 查找证据。Citation 缺失时不使用数组下标猜测，
  // 否则模型生成的错误编号可能让侧栏展示另一条真实但不相关的原文。
  const result = citation
    ? results.find((item) => item.chunk_id === citation.chunk_id)
    : undefined;

  return (
    <Dialog open={sourceNumber !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        showCloseButton={false}
        overlayClassName="bg-black/20 backdrop-blur-[1px] md:bg-transparent md:backdrop-blur-none"
        className="inset-y-0 left-auto right-0 top-0 grid h-dvh w-full max-w-[34rem] translate-x-0 translate-y-0 grid-rows-[auto_minmax(0,1fr)] gap-0 overflow-hidden rounded-none border-y-0 border-r-0 p-0 shadow-2xl sm:w-[min(34rem,92vw)]"
      >
        <DialogHeader className="h-14 min-w-0 shrink-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-4 border-b border-border px-4 text-left">
          <div className="min-w-0">
            <DialogTitle className="flex items-center gap-2 text-sm font-semibold">
              <BookOpen className="size-4" /> 来源 {sourceNumber ?? ""}
            </DialogTitle>
            <DialogDescription className="truncate text-xs leading-5">
              {citation?.filename ?? "来源不存在"}
            </DialogDescription>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭来源侧栏">
            <X className="size-4" />
          </Button>
        </DialogHeader>

        <div className="chat-scrollbar flex-1 space-y-5 overflow-y-auto bg-[#fafafa] p-4 sm:p-5 dark:bg-[#1d1d1d]">
          {!result ? (
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              这条历史消息没有保存对应的检索快照，无法展示来源详情。
            </p>
          ) : (
            <>
              <section className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">
                  原文位置
                </p>
                <p className="text-sm font-medium">
                  {formatLocator(
                    citation?.locator ?? result.locator,
                    citation?.heading_path ?? result.heading_path,
                  )}
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {result.content_types.includes("IMAGE") && (
                    <Badge variant="secondary" className="gap-1">
                      <ImageIcon className="size-3" /> 图片
                    </Badge>
                  )}
                  {result.content_types.includes("TABLE") && (
                    <Badge variant="secondary" className="gap-1">
                      <Table2 className="size-3" /> 表格
                    </Badge>
                  )}
                  <Badge variant="outline" className="font-mono">
                    score {result.score.toFixed(4)}
                  </Badge>
                </div>
              </section>

              {result.assets.map((asset) => (
                <figure key={asset.id} className="overflow-hidden rounded-2xl border border-border bg-white">
                  <img
                    src={`${API_URL}${asset.content_url}`}
                    alt={asset.title}
                    loading="lazy"
                    className="max-h-[32rem] w-full object-contain"
                  />
                  <figcaption className="border-t border-border bg-background px-3 py-2 text-xs text-muted-foreground">
                    {asset.title}
                  </figcaption>
                </figure>
              ))}

              {result.assets.length === 0 && result.preview_url && (
                <a
                  href={`${API_URL}${result.preview_url}`}
                  target="_blank"
                  rel="noreferrer"
                  className="block overflow-hidden rounded-2xl border border-border bg-white"
                >
                  <img
                    src={`${API_URL}${result.preview_url}`}
                    alt={`${result.filename} 原文证据`}
                    loading="lazy"
                    className="max-h-[32rem] w-full object-contain"
                  />
                </a>
              )}

              <section>
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  检索内容
                </p>
                <div className="prose-chat rounded-2xl border border-border bg-background p-4 text-sm leading-7">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {withoutAssetMarkers(result.matched_content ?? result.content)}
                  </ReactMarkdown>
                </div>
              </section>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
