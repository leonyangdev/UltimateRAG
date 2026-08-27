import { Bot, User } from "lucide-react";

import type { Citation, RAGMessage as RAGMessageType, RetrievalResult } from "@/app/lib";
import { RetrievalEvidence } from "@/components/retrieval-evidence";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

interface RAGMessageProps {
  message: RAGMessageType;
}

/**
 * 按 AI SDK 的 Message Part 渲染一次对话。
 * 文本与 data-retrieval 属于同一个 assistant message，因此流式更新时不会把上一轮证据
 * 错配到新答案；未知 Part 有意忽略，便于未来增加 Tool Call 时独立扩展渲染器。
 */
export function RAGMessage({ message }: RAGMessageProps) {
  const isUser = message.role === "user";
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");

  let citations: Citation[] = [];
  let results: RetrievalResult[] = [];
  for (const part of message.parts) {
    if (part.type === "data-retrieval") {
      citations = part.data.citations;
      results = part.data.retrieval_results;
    }
  }

  return (
    <article className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <Avatar className="mt-0.5 size-8 border border-border bg-foreground text-background">
          <AvatarFallback className="bg-foreground text-background">
            <Bot className="size-4" />
          </AvatarFallback>
        </Avatar>
      )}

      <div className={`min-w-0 max-w-[88%] space-y-3 md:max-w-[78%] ${isUser ? "order-first" : ""}`}>
        <div
          className={
            isUser
              ? "rounded-2xl rounded-tr-md bg-foreground px-4 py-3 text-sm leading-6 text-background shadow-sm"
              : "rounded-2xl rounded-tl-md border border-border/70 bg-card px-4 py-3 text-sm leading-6 text-foreground shadow-sm"
          }
        >
          <p className="whitespace-pre-wrap">{text}</p>
        </div>
        {!isUser && <RetrievalEvidence citations={citations} results={results} />}
      </div>

      {isUser && (
        <Avatar className="mt-0.5 size-8 border border-border">
          <AvatarFallback>
            <User className="size-4" />
          </AvatarFallback>
        </Avatar>
      )}
    </article>
  );
}
