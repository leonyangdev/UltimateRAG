"use client";

import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  Database,
  FileSearch,
  MessageSquareText,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * 首页 —— 当前项目的简单介绍。
 *
 * 职责边界：
 *   只承担「这是什么产品、存储如何支撑可追溯」的介绍职责，不加载任何业务数据。
 *   知识库的枚举与创建在 /knowledge-bases 完成，问答在 /chat 完成；
 *   页面内的按钮只负责把访客引导到这两个工作入口。
 *
 * 视觉背景：
 *   全页使用统一的页面底色，不使用突然截断的装饰渐变，也不做灰色分区色带；
 *   区块之间只靠留白分隔，保证首屏到页尾背景连续、无生硬的分界线。
 *
 * 排版基准：
 *   全站杜绝 14px 以下字号。本页面的层级为：正文 16px（text-base）、
 *   次要说明 14px（text-sm）、区块标题 18~30px、主标题保持大字号展示。
 */

export default function Home() {
  return (
    <div>
      {/* ─── 第一屏：产品介绍（居中排版） ─── */}
      <section className="mx-auto max-w-3xl px-4 pb-24 pt-20 text-center sm:px-6 md:pt-28">
        <Badge variant="outline" className="gap-2 px-3.5 py-2">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-60" />
            <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
          </span>
          检索链路已就绪
        </Badge>

        <h1 className="mt-7 text-balance text-5xl font-semibold leading-[1.05] tracking-[-0.045em] sm:text-6xl lg:text-7xl">
          让企业知识成为
          <span className="block text-primary">可验证的智能答案</span>
        </h1>

        <p className="mx-auto mt-7 max-w-2xl text-pretty text-lg leading-8 text-muted-foreground sm:text-xl">
          从多格式文档智能、Dense + BM25 混合检索到重排与流式生成，完整保留每一次回答的证据链。
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Button size="lg" asChild className="gap-2 text-base shadow-lg shadow-primary/15">
            <Link href="/knowledge-bases">
              进入知识库
              <ArrowRight />
            </Link>
          </Button>
          <Button size="lg" variant="outline" asChild className="gap-2 text-base">
            <Link href="/chat">
              <MessageSquareText />
              开始问答
            </Link>
          </Button>
        </div>

        <div className="mt-10 flex flex-wrap justify-center gap-x-7 gap-y-3 text-base text-muted-foreground">
          {["来源可追溯", "知识库隔离", "失败状态透明"].map((item) => (
            <span key={item} className="inline-flex items-center gap-2">
              <CheckCircle2 className="size-4.5 text-emerald-600" />
              {item}
            </span>
          ))}
        </div>
      </section>

      {/* ─── 第二屏：存储架构（与第一屏同底色，仅以留白分隔） ─── */}
      <section className="mx-auto max-w-7xl px-4 pb-20 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-2xl font-semibold tracking-tight sm:text-3xl">
            为可追溯而设计的三层存储
          </h2>
          <p className="mt-4 text-base leading-7 text-muted-foreground">
            业务事实、原始文件与派生索引各归其位：任何回答都能回溯到原文，
            向量索引永远可以由事实数据重建。
          </p>
        </div>

        <div className="mt-12 grid gap-4 md:grid-cols-3">
          {[
            [Database, "PostgreSQL", "业务事实、文档状态与 Chunk 元数据", "文档是否可检索、属于哪个知识库，都以这里记录的事实为准。"],
            [FileSearch, "MinIO", "可排查、可重建的原始文档事实", "用户上传的原文原样保存，即使处理失败也能据此排查或重新索引。"],
            [Sparkles, "Milvus", "按知识库隔离的 Dense + BM25 索引", "只保存可重建的派生索引，语义召回与精确词召回可以独立解释。"],
          ].map(([Icon, title, copy, detail]) => {
            const ArchitectureIcon = Icon as typeof Database;
            return (
              <div key={title as string} className="rounded-2xl border bg-card p-6 shadow-sm">
                <span className="grid size-11 place-items-center rounded-xl bg-primary/10 text-primary">
                  <ArchitectureIcon className="size-5" />
                </span>
                <h3 className="mt-5 text-lg font-medium">{title as string}</h3>
                <p className="mt-1.5 text-base font-medium text-foreground">{copy as string}</p>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">{detail as string}</p>
              </div>
            );
          })}
        </div>
      </section>

      {/* ─── 收尾 ─── */}
      <p className="pb-16 text-center text-sm text-muted-foreground">
        UltimateRAG · 从可信文档到可追溯回答
      </p>
    </div>
  );
}
