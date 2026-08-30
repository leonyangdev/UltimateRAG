import { BookOpen, ChevronDown, GitMerge, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
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

/**
 * 展示可追溯的检索证据，而不是只给用户一个不可验证的模型答案。
 * 使用原生 details 保留键盘可访问性，并避免为简单折叠行为引入额外客户端状态。
 */
export function RetrievalEvidence({
  citations = [],
  results,
  trace = null,
  defaultOpen = false,
}: RetrievalEvidenceProps) {
  if (results.length === 0 && !trace) return null;

  return (
    <details className="group" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between rounded-lg px-1 py-2 text-sm font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <BookOpen className="size-4 text-primary" />
          检索证据
          <Badge variant="secondary" className="rounded-md font-mono text-sm">
            TOP {results.length}
          </Badge>
        </span>
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
      </summary>

      <div className="mt-2 grid gap-2">
        {trace && (
          <Card className="border-primary/20 bg-primary/5 py-0 shadow-none">
            <CardContent className="space-y-2 p-4 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <GitMerge className="size-4 text-primary" />
                <Badge variant="secondary">{trace.mode.toUpperCase()}</Badge>
                <span className="text-muted-foreground">
                  {trace.candidate_count} 个候选 → {trace.result_count} 个结果
                </span>
                {trace.rewrite_applied && <Badge variant="outline">Query Rewrite</Badge>}
                {trace.rerank_applied && <Badge variant="outline">Rerank</Badge>}
                {trace.parent_expansion_applied && <Badge variant="outline">Small2Big</Badge>}
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
          const locator = citation?.locator ?? result.locator;
          const heading = formatLocator(locator, citation?.heading_path ?? result.heading_path);

          return (
            <Card key={result.chunk_id} className="border-border/70 bg-muted/35 py-0 shadow-none">
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-foreground">
                      [{index + 1}] {result.filename}
                    </p>
                    <p className="mt-1 truncate text-sm text-muted-foreground">
                      {heading}
                    </p>
                  </div>
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
                <p className="line-clamp-4 whitespace-pre-wrap text-base leading-7 text-muted-foreground">
                  {result.content}
                </p>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </details>
  );
}
