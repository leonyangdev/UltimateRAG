import { BookOpen, ChevronDown } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Citation, RetrievalResult } from "@/app/lib";

interface RetrievalEvidenceProps {
  citations?: Citation[];
  results: RetrievalResult[];
  defaultOpen?: boolean;
}

/**
 * 展示可追溯的检索证据，而不是只给用户一个不可验证的模型答案。
 * 使用原生 details 保留键盘可访问性，并避免为简单折叠行为引入额外客户端状态。
 */
export function RetrievalEvidence({
  citations = [],
  results,
  defaultOpen = false,
}: RetrievalEvidenceProps) {
  if (results.length === 0) return null;

  return (
    <details className="group" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between rounded-lg px-1 py-2 text-sm font-medium text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <BookOpen className="size-4 text-primary" />
          检索证据
          <Badge variant="secondary" className="rounded-md font-mono text-[10px]">
            TOP {results.length}
          </Badge>
        </span>
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" />
      </summary>

      <div className="mt-2 grid gap-2">
        {results.map((result, index) => {
          const citation = citations.find((item) => item.chunk_id === result.chunk_id);
          const heading = (citation?.heading_path ?? result.heading_path).join(" / ");

          return (
            <Card key={result.chunk_id} className="border-border/70 bg-muted/35 py-0 shadow-none">
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-foreground">
                      [{index + 1}] {result.filename}
                    </p>
                    <p className="mt-1 truncate text-[11px] text-muted-foreground">
                      {heading || "未命名章节"}
                    </p>
                  </div>
                  <Badge variant="outline" className="shrink-0 font-mono text-[10px] tabular-nums">
                    {result.score.toFixed(4)}
                  </Badge>
                </div>
                <p className="line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
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
