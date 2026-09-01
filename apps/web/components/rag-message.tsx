"use client";

import { Bot, Check, Copy } from "lucide-react";
import { useState } from "react";

import type {
  Citation,
  RAGMessage as RAGMessageType,
  RetrievalResult,
  RetrievalTrace,
} from "@/app/lib";
import { RetrievalEvidence } from "@/components/retrieval-evidence";
import { AnswerMarkdown } from "@/components/answer-markdown";
import { SourceSidebar } from "@/components/source-sidebar";
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
  const [selectedSource, setSelectedSource] = useState<number | null>(null);
  const [isCopied, setIsCopied] = useState(false);
  const isUser = message.role === "user";
  const text = message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");

  let citations: Citation[] = [];
  let results: RetrievalResult[] = [];
  let trace: RetrievalTrace | null = null;
  for (const part of message.parts) {
    if (part.type === "data-retrieval") {
      citations = part.data.citations;
      results = part.data.retrieval_results;
      trace = part.data.retrieval_trace;
    }
  }

  /**
   * 复制动作只处理当前消息已经渲染的纯文本，不复制隐藏的 Retrieval 快照或内部 Asset ID。
   * Clipboard API 失败不会遮挡答案；按钮保持原图标，让用户可以再次尝试或手动选择正文。
   */
  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(text);
      setIsCopied(true);
      window.setTimeout(() => setIsCopied(false), 1600);
    } catch {
      setIsCopied(false);
    }
  }

  return (
    <article className={`group flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <Avatar className="mt-0.5 size-7 shrink-0 border-0 bg-foreground text-background shadow-sm">
          <AvatarFallback className="bg-foreground text-background">
            <Bot className="size-3.5" />
          </AvatarFallback>
        </Avatar>
      )}

      <div className={`min-w-0 space-y-3 ${isUser ? "max-w-[85%] sm:max-w-[75%]" : "flex-1"}`}>
        {isUser ? (
          /* 用户消息保留轻量灰色气泡；助手答案继续使用无背景正文，形成明确角色层级。 */
          <div className="rounded-[20px] bg-[#f4f4f4] px-4 py-2.5 text-[15px] leading-6 text-foreground dark:bg-[#2f2f2f]">
            <p className="whitespace-pre-wrap">{text}</p>
          </div>
        ) : (
          <>
            <div className="prose-chat text-[15px] leading-7 text-foreground">
              <AnswerMarkdown
                content={text}
                results={results}
                onCitationClick={setSelectedSource}
              />
            </div>
            {text && (
              <div className="flex min-h-8 items-center gap-1 text-muted-foreground opacity-100 transition-opacity sm:opacity-0 sm:group-hover:opacity-100 sm:group-focus-within:opacity-100">
                <button
                  type="button"
                  onClick={() => void copyAnswer()}
                  className="grid size-8 place-items-center rounded-lg hover:bg-muted hover:text-foreground"
                  aria-label={isCopied ? "回答已复制" : "复制回答"}
                  title={isCopied ? "已复制" : "复制"}
                >
                  {isCopied ? <Check className="size-4" /> : <Copy className="size-4" />}
                </button>
              </div>
            )}
          </>
        )}
        {!isUser && <RetrievalEvidence citations={citations} results={results} trace={trace} />}
      </div>
      {!isUser && (
        <SourceSidebar
          sourceNumber={selectedSource}
          citations={citations}
          results={results}
          onClose={() => setSelectedSource(null)}
        />
      )}
    </article>
  );
}
