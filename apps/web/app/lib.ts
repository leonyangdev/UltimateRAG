import type { UIMessage } from "ai";

/**
 * 解析浏览器应访问的 FastAPI 地址。
 *
 * NEXT_PUBLIC_API_URL 在 Next.js 构建时内联，适合反向代理或独立域名部署。开发者没有
 * 显式配置时，客户端沿用当前页面的协议和主机名并切换到 8000 端口：这样 localhost
 * 开发与通过 192.168.3.19 访问局域网 Web 都能连接同一台主机上的 API。
 * 服务端预渲染期间没有 window，回退值只用于生成静态页面，不会发起业务请求。
 */
function resolveApiUrl(): string {
  const configuredUrl = process.env.NEXT_PUBLIC_API_URL?.trim();
  if (configuredUrl) return configuredUrl.replace(/\/$/, "");
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "http://localhost:8000";
}

export const API_URL = resolveApiUrl();

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

export interface DocumentItem {
  id: string;
  filename: string;
  status: string;
  error_message: string | null;
  parser_name: string | null;
  parser_version: string | null;
  created_at: string;
}

/** 不同来源格式共享的可选原文位置。 */
export interface SourceLocator {
  heading_path: string[];
  page: number | null;
  bbox: number[] | null;
  sheet: string | null;
  cell_range: string | null;
  slide: number | null;
}

export interface RetrievalResult {
  chunk_id: string;
  document_id: string;
  filename: string;
  content: string;
  heading_path: string[];
  locator: SourceLocator | null;
  score: number;
}

export interface Citation {
  document_id: string;
  filename: string;
  chunk_id: string;
  heading_path: string[];
  locator: SourceLocator | null;
}

/** 把格式特有定位转换为用户可读短文本，顺序与后端 Prompt 保持一致。 */
export function formatLocator(locator: SourceLocator | null, fallback: string[]): string {
  if (!locator) return fallback.join(" / ") || "未提供原文定位";
  const parts: string[] = [];
  if (locator.heading_path.length) parts.push(locator.heading_path.join(" / "));
  if (locator.page !== null) parts.push(`第 ${locator.page} 页`);
  if (locator.sheet) parts.push(`工作表 ${locator.sheet}`);
  if (locator.cell_range) parts.push(`区域 ${locator.cell_range}`);
  if (locator.slide !== null) parts.push(`第 ${locator.slide} 张幻灯片`);
  return parts.join(" · ") || "未提供原文定位";
}

export interface ChatResult {
  answer: string;
  citations: Citation[];
  retrieval_results: RetrievalResult[];
}

/**
 * 与 FastAPI ``data-retrieval`` Part 对应的 AI SDK 数据类型。
 * 引用和完整召回结果随 assistant message 一起到达，刷新 React 状态时不会与文本流错配。
 */
export type RAGDataParts = {
  retrieval: {
    citations: Citation[];
    retrieval_results: RetrievalResult[];
  };
};

export type RAGMessage = UIMessage<unknown, RAGDataParts>;

/**
 * 调用 FastAPI 并统一解析结构化错误。
 * FormData 请求必须由浏览器自动生成 multipart boundary，因此不会手工设置 Content-Type。
 */
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? init.headers
        : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: "请求失败" }));
    throw new Error(payload.detail ?? `请求失败 (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
