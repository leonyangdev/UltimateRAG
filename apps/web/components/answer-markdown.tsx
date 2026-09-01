"use client";

/* Asset 图片来自运行时 API，尺寸取决于原 PDF 裁图，无法使用 next/image 的静态尺寸契约。 */
/* eslint-disable @next/next/no-img-element */

import { useMemo, useState } from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

import { API_URL, type DocumentAsset, type RetrievalResult } from "@/app/lib";

interface AnswerMarkdownProps {
  content: string;
  results: RetrievalResult[];
  onCitationClick: (sourceNumber: number) => void;
}

interface AssetImageProps {
  asset: DocumentAsset;
  alt: string;
}

/** 渲染一个由后端证据清单确认的图片；失败时保留可理解的文本占位。 */
function AssetImage({ asset, alt }: AssetImageProps) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="my-3 block rounded-xl border border-dashed border-border px-4 py-3 text-sm text-muted-foreground">
        图片《{asset.title}》暂时加载失败，可点击相邻来源链接查看原文证据。
      </span>
    );
  }
  return (
    <figure className="my-5 overflow-hidden rounded-2xl border border-border bg-white">
      <img
        src={`${API_URL}${asset.content_url}`}
        alt={alt || asset.title}
        loading="lazy"
        onError={() => setFailed(true)}
        className="max-h-[36rem] w-full object-contain"
      />
      <figcaption className="border-t border-border bg-background px-3.5 py-2.5 text-xs text-muted-foreground">
        {asset.title}
      </figcaption>
    </figure>
  );
}

/**
 * 渲染模型答案中的受控富媒体协议。
 *
 * ``citation://N`` 不发起网络请求，而是打开第 N 个来源侧栏；``asset://ID`` 只有在同一条
 * 消息的 RetrievalResult 明确声明该 ID 时才会转成 API 图片。模型或恶意文档即使输出任意
 * Asset ID/外链图片，也无法绕过这份后端证据白名单触发对象读取或第三方跟踪请求。
 */
export function AnswerMarkdown({ content, results, onCitationClick }: AnswerMarkdownProps) {
  const assets = useMemo(
    () => new Map(results.flatMap((result) => result.assets).map((asset) => [asset.id, asset])),
    [results],
  );

  // 兼容升级前已经保存的“[来源 N]”纯文本答案。新 Prompt 会直接产生 citation:// 链接；
  // 负向检查避免把已经是 Markdown 链接的来源再次包装。
  const normalized = content.replace(
    /\[来源\s+(\d+)\](?!\s*\()/g,
    (_match, sourceNumber: string) => `[来源 ${sourceNumber}](citation://${sourceNumber})`,
  );

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(url) => {
        if (url.startsWith("asset://") || url.startsWith("citation://")) return url;
        return defaultUrlTransform(url);
      }}
      components={{
        a({ href, children }) {
          if (href?.startsWith("citation://")) {
            const sourceNumber = Number.parseInt(href.slice("citation://".length), 10);
            if (Number.isInteger(sourceNumber) && sourceNumber > 0) {
              return (
                <button
                  type="button"
                  onClick={() => onCitationClick(sourceNumber)}
                  className="mx-0.5 inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-semibold text-foreground no-underline transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  aria-label={`打开来源 ${sourceNumber}`}
                >
                  {children}
                </button>
              );
            }
          }
          return (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          );
        },
        img({ src, alt }) {
          if (typeof src !== "string" || !src.startsWith("asset://")) {
            // RAG 文档和模型输出都是不可信输入。禁止自动加载任意公网图片，避免通过图片
            // 请求泄露用户 IP、Referer 或查询语义；普通外部链接仍可由用户主动点击。
            return <span className="text-sm text-muted-foreground">外部图片已拦截</span>;
          }
          const asset = assets.get(src.slice("asset://".length));
          return asset ? (
            <AssetImage asset={asset} alt={alt ?? asset.title} />
          ) : (
            <span className="text-sm text-muted-foreground">图片资源不属于当前检索证据</span>
          );
        },
      }}
    >
      {normalized}
    </ReactMarkdown>
  );
}
