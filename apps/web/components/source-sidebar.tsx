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
 */
export function SourceSidebar({ sourceNumber, citations, results, onClose }: SourceSidebarProps) {
  if (sourceNumber === null) return null;
  const citation = citations[sourceNumber - 1];
  const result = citation
    ? results.find((item) => item.chunk_id === citation.chunk_id)
    : results[sourceNumber - 1];

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="关闭来源侧栏"
        onClick={onClose}
        className="absolute inset-0 bg-black/25 backdrop-blur-[1px]"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`来源 ${sourceNumber}`}
        className="absolute inset-y-0 right-0 flex w-full max-w-xl flex-col border-l border-border bg-background shadow-2xl"
      >
        <header className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <BookOpen className="size-4 text-primary" /> 来源 {sourceNumber}
            </div>
            <p className="mt-1 truncate text-sm text-muted-foreground">
              {citation?.filename ?? result?.filename ?? "来源不存在"}
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭来源侧栏">
            <X className="size-4" />
          </Button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-5">
          {!result ? (
            <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
              这条历史消息没有保存对应的检索快照，无法展示来源详情。
            </p>
          ) : (
            <>
              <section className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
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
                <figure key={asset.id} className="overflow-hidden rounded-xl border border-border bg-white">
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
                  className="block overflow-hidden rounded-xl border border-border bg-white"
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
                <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  检索内容
                </p>
                <div className="prose-chat rounded-xl border border-border/70 bg-muted/30 p-4 text-sm leading-7">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {withoutAssetMarkers(result.matched_content ?? result.content)}
                  </ReactMarkdown>
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
    </div>
  );
}
