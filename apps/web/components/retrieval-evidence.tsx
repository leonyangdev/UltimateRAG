"use client";

/* PDF 预览来自运行时 API 且尺寸由原文 BBox 决定，无法满足 next/image 的静态尺寸契约。 */
/* eslint-disable @next/next/no-img-element */

import { useState } from "react";
import { BookOpen, ChevronDown, GitMerge, ImageIcon, Table2, TriangleAlert } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  API_URL,
  formatLocator,
  type Citation,
  type RetrievalResult,
  type RetrievalTrace,
} from "@/app/lib";

interface RetrievalEvidenceProps {
  citations?: Citation[];
  results: RetrievalResult[];
  trace?: RetrievalTrace | null;
  defaultOpen?: boolean;
  onSourceClick?: (sourceNumber: number) => void;
}

const fallbackLabels: Record<string, string> = {
  query_rewriter_unavailable: "未配置查询改写器",
  query_rewrite_failed: "查询改写失败，已使用原查询",
  dense_retrieval_failed: "Dense 通道失败",
  sparse_retrieval_failed: "Sparse 通道失败",
  reranker_unavailable: "未配置重排器",
  rerank_failed: "重排失败，已保留融合顺序",
  parent_expansion_failed: "上下文扩展失败，已使用命中块",
};

interface EvidencePreviewProps {
  result: RetrievalResult;
}

/**
 * 移除证据正文中的受控图片标记。
 *
 * 图片已由 EvidencePreview 使用后端白名单 URL 单独渲染；若再把 ``asset://`` 交给
 * 通用 Markdown 渲染器，浏览器既无法直接加载自定义协议，也会在证据卡里产生重复图片。
 */
function withoutAssetMarkers(content: string): string {
  return content.replace(/!\[[^\]]*\]\(asset:\/\/[^)]+\)\s*/g, "");
}

/**
 * 延迟加载 PDF 命中区域。预览失败只影响辅助证据，不隐藏已经召回的结构化文本。
 */
function EvidencePreview({ result }: EvidencePreviewProps) {
  const [failed, setFailed] = useState(false);
  const asset = result.assets[0];
  const previewUrl = asset?.content_url ?? result.preview_url;
  if (!previewUrl || failed) return null;

  const isImage = result.content_types.includes("IMAGE");
  const isTable = result.content_types.includes("TABLE");
  const label = asset?.title ?? (isImage ? "PDF 图片原文" : isTable ? "PDF 表格原文" : "PDF 原文区域");

  return (
    <figure className="overflow-hidden rounded-xl border border-border bg-background">
      <div className="flex items-center gap-2 border-b border-border/70 px-3 py-2 text-xs font-medium text-muted-foreground">
        {isTable ? <Table2 className="size-3.5" /> : <ImageIcon className="size-3.5" />}
        {label}
      </div>
      <a
        href={`${API_URL}${previewUrl}`}
        target="_blank"
        rel="noreferrer"
        className="block bg-white"
        title="在新窗口查看原文证据"
      >
        <img
          src={`${API_URL}${previewUrl}`}
          alt={`${result.filename} ${label}`}
          loading="lazy"
          onError={() => setFailed(true)}
          className="max-h-[28rem] w-full object-contain"
        />
      </a>
      <figcaption className="border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
        {asset
          ? "摄取期从原文抽取并持久化，可由答案中的 asset:// 引用复用。"
          : "由原 PDF 的页码与版面坐标本地裁切；点击可查看大图。"}
      </figcaption>
    </figure>
  );
}

/**
 * 展示可追溯的检索证据，而不是只给用户一个不可验证的模型答案。
 * 使用原生 details 保留键盘可访问性，并避免为简单折叠行为引入额外客户端状态。
 * 在回答场景中，结果标题按 Citation 的真实顺序打开右侧来源栏；检索调试场景没有
 * Citation 快照，因此仍显示卡片，但不会把结果数组下标伪装成答案来源编号。
 */
export function RetrievalEvidence({
  citations = [],
  results,
  trace = null,
  defaultOpen = false,
  onSourceClick,
}: RetrievalEvidenceProps) {
  if (results.length === 0 && !trace) return null;

  return (
    <details className="group/evidence" open={defaultOpen}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <BookOpen className="size-3.5" />
        {results.length} 个来源
        <ChevronDown className="size-3.5 text-muted-foreground transition-transform group-open/evidence:rotate-180" />
      </summary>

      <div className="mt-3 grid gap-2">
        {trace && (
          <Card className="border-primary/20 bg-primary/5 py-0 shadow-none">
            <CardContent className="space-y-2 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <GitMerge className="size-4 text-primary" />
                <Badge variant="secondary">{trace.mode.toUpperCase()}</Badge>
                <Badge variant="outline">
                  {trace.intent === "document_summary" ? "全文总结" : "事实问答"}
                </Badge>
                <span className="text-muted-foreground">
                  {trace.candidate_count} 个候选 → {trace.result_count} 个结果
                </span>
                {trace.rewrite_applied && <Badge variant="outline">Query Rewrite</Badge>}
                {trace.rerank_applied && <Badge variant="outline">Rerank</Badge>}
                {trace.parent_expansion_applied && <Badge variant="outline">Small2Big</Badge>}
                {trace.strategy === "structural_coverage" && (
                  <Badge variant="outline">章节覆盖</Badge>
                )}
              </div>
              {trace.query_variants.length > 1 && (
                <p className="text-muted-foreground">
                  改写查询：<span className="text-foreground">{trace.query_variants[1]}</span>
                </p>
              )}
              {trace.fallback_reasons.length > 0 && (
                <div className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                  <span>
                    {trace.fallback_reasons
                      .map((reason) => fallbackLabels[reason] ?? reason)
                      .join("；")}
                  </span>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {results.map((result, index) => {
          const citation = citations.find((item) => item.chunk_id === result.chunk_id);
          const citationIndex = citations.findIndex((item) => item.chunk_id === result.chunk_id);
          const sourceNumber = citationIndex >= 0 ? citationIndex + 1 : null;
          const locator = citation?.locator ?? result.locator;
          const heading = formatLocator(locator, citation?.heading_path ?? result.heading_path);

          return (
            <Card key={result.chunk_id} className="rounded-2xl border-border bg-background py-0 shadow-none">
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  {sourceNumber !== null && onSourceClick ? (
                    <button
                      type="button"
                      onClick={() => onSourceClick(sourceNumber)}
                      aria-label={`打开来源 ${sourceNumber}：${result.filename}`}
                      aria-haspopup="dialog"
                      className="min-w-0 flex-1 rounded-lg text-left outline-none transition-colors hover:text-primary focus-visible:ring-2 focus-visible:ring-ring/50"
                    >
                      <span className="block truncate text-sm font-semibold text-foreground">
                        [来源 {sourceNumber}] {result.filename}
                      </span>
                      <span className="mt-1 block truncate text-sm text-muted-foreground">
                        {heading}
                      </span>
                    </button>
                  ) : (
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-semibold text-foreground">
                        [{index + 1}] {result.filename}
                      </p>
                      <p className="mt-1 truncate text-sm text-muted-foreground">{heading}</p>
                    </div>
                  )}
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <Badge variant="outline" className="font-mono text-sm tabular-nums">
                      最终 {result.score.toFixed(4)}
                    </Badge>
                    {result.context_chunk_ids.length > 1 && (
                      <span className="text-xs text-muted-foreground">
                        上下文 {result.context_chunk_ids.length} 块
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {result.retrieval_sources.map((source) => (
                    <Badge key={source} variant="secondary" className="font-mono text-xs">
                      {source}
                    </Badge>
                  ))}
                  {result.content_types.includes("IMAGE") && (
                    <Badge variant="secondary" className="gap-1 text-xs">
                      <ImageIcon className="size-3" /> 图片
                    </Badge>
                  )}
                  {result.content_types.includes("TABLE") && (
                    <Badge variant="secondary" className="gap-1 text-xs">
                      <Table2 className="size-3" /> 表格
                    </Badge>
                  )}
                  {result.dense_score !== null && (
                    <Badge variant="outline" className="font-mono text-xs">
                      Dense {result.dense_score.toFixed(4)}
                    </Badge>
                  )}
                  {result.sparse_score !== null && (
                    <Badge variant="outline" className="font-mono text-xs">
                      BM25 {result.sparse_score.toFixed(4)}
                    </Badge>
                  )}
                  {result.fusion_score !== null && (
                    <Badge variant="outline" className="font-mono text-xs">
                      RRF {result.fusion_score.toFixed(4)}
                    </Badge>
                  )}
                  {result.rerank_score !== null && (
                    <Badge variant="outline" className="font-mono text-xs">
                      Rerank {result.rerank_score.toFixed(4)}
                    </Badge>
                  )}
                </div>
                <EvidencePreview result={result} />
                <div className="prose-chat max-h-72 overflow-auto text-sm leading-7 text-muted-foreground [&_table]:text-sm">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {withoutAssetMarkers(result.matched_content ?? result.content)}
                  </ReactMarkdown>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </details>
  );
}
