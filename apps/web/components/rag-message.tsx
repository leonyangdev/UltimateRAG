import { Bot, User } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Citation, RAGMessage as RAGMessageType, RetrievalResult } from "@/app/lib";
import { RetrievalEvidence } from "@/components/retrieval-evidence";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

interface RAGMessageProps {
  message: RAGMessageType;
}

/**
 * 按 AI SDK 的 Message Part 渲染一次对话。
 *
 * 助手消息使用 ReactMarkdown + remark-gfm 渲染，支持标题、列表、表格、代码块等。
 * 用户消息保持纯文本气泡，与 ChatGPT 体验一致。
 *
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
        <Avatar className="mt-1 size-7 shrink-0 border border-border bg-foreground text-background">
          <AvatarFallback className="bg-foreground text-background">
            <Bot className="size-3.5" />
          </AvatarFallback>
        </Avatar>
      )}

      <div className={`min-w-0 space-y-3 ${isUser ? "max-w-[80%] order-first" : ""}`}>
        {isUser ? (
          /* 用户消息：柔和圆角气泡，纯文本展示 */
          <div className="rounded-2xl rounded-tr-md bg-secondary px-4 py-3 text-sm leading-6 text-foreground">
            <p className="whitespace-pre-wrap">{text}</p>
          </div>
        ) : (
          /* 助手消息：ChatGPT 风格，无背景，Markdown 渲染 */
          <div className="prose-chat text-sm leading-7 text-foreground">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        )}
        {!isUser && <RetrievalEvidence citations={citations} results={results} />}
      </div>

      {isUser && (
        <Avatar className="mt-1 size-7 shrink-0 border border-border">
          <AvatarFallback>
            <User className="size-3.5" />
          </AvatarFallback>
        </Avatar>
      )}
    </article>
  );
}
